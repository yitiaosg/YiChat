"""Persistent YCards auctions; all funds use the existing points ledger.

No independent balance: bids move spendable points into durable escrow rows.
Settlement refunds escrow and posts the actual price/fee in the same transaction.
"""
import hashlib
import time
import uuid
from decimal import Decimal, InvalidOperation, ROUND_CEILING

import ycards as yc
import yidt_core as y

AUCTION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS yc_auctions(
 id TEXT PRIMARY KEY, instance TEXT NOT NULL REFERENCES yc_instances(id), seller TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('sealed','public')),
 pricing TEXT NOT NULL CHECK(pricing IN ('first','second')),
 clock TEXT NOT NULL CHECK(clock IN ('fixed','rolling')),
 start_cents INTEGER NOT NULL CHECK(start_cents>0), increment_cents INTEGER NOT NULL,
 duration_seconds INTEGER NOT NULL, rolling_seconds INTEGER NOT NULL, extension_seconds INTEGER NOT NULL,
 created REAL NOT NULL, ends REAL NOT NULL,
 state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','sold','unsold','cancelled')),
 winner TEXT, price_cents INTEGER, fee_cents INTEGER, finished REAL,
 request_key TEXT NOT NULL, request_hash TEXT NOT NULL, UNIQUE(seller,request_key));
CREATE UNIQUE INDEX IF NOT EXISTS yc_auction_active_instance ON yc_auctions(instance) WHERE state='active';
CREATE INDEX IF NOT EXISTS yc_auction_due ON yc_auctions(state,ends);
CREATE INDEX IF NOT EXISTS yc_auction_seller ON yc_auctions(seller,created);
CREATE TABLE IF NOT EXISTS yc_bid_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, auction TEXT NOT NULL REFERENCES yc_auctions(id),
 user TEXT NOT NULL, amount_cents INTEGER NOT NULL, created REAL NOT NULL, request_key TEXT NOT NULL,
 UNIQUE(user,request_key));
CREATE INDEX IF NOT EXISTS yc_bid_event_auction ON yc_bid_events(auction,id);
CREATE TABLE IF NOT EXISTS yc_bids(
 auction TEXT NOT NULL REFERENCES yc_auctions(id), user TEXT NOT NULL,
 amount_cents INTEGER NOT NULL, held_cents INTEGER NOT NULL CHECK(held_cents>=0),
 priority INTEGER NOT NULL REFERENCES yc_bid_events(id), PRIMARY KEY(auction,user));
CREATE INDEX IF NOT EXISTS yc_bid_user ON yc_bids(user,auction);
-- Cross-table exclusion also protects against older web workers during deployment.
CREATE TRIGGER IF NOT EXISTS yc_no_listing_during_auction BEFORE INSERT ON yc_listings
 WHEN NEW.state='active' AND EXISTS(SELECT 1 FROM yc_auctions WHERE instance=NEW.instance AND state='active')
 BEGIN SELECT RAISE(ABORT,'card is in auction'); END;
CREATE TRIGGER IF NOT EXISTS yc_no_auction_during_listing BEFORE INSERT ON yc_auctions
 WHEN NEW.state='active' AND EXISTS(SELECT 1 FROM yc_listings WHERE instance=NEW.instance AND state='active')
 BEGIN SELECT RAISE(ABORT,'card is listed'); END;
'''


def fee(cents):
    return ((cents + 19999)//20000)*100


def hours(value, label, maximum, zero=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise yc.CardError(f'{label}无效')
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (n == 0 and not zero) or n > maximum:
            raise yc.CardError(f'{label}须大于 0 且最多 {maximum} 小时' if not zero else f'{label}须为 0～{maximum} 小时')
        return int((n*3600).to_integral_value(rounding=ROUND_CEILING))
    except InvalidOperation as exc:
        raise yc.CardError(f'{label}无效') from exc


def key(value):
    return yc.identifier(value, '请求编号', r'[a-zA-Z0-9_-]{16,100}')


class AuctionMixin:
    def create_auction(self, user, data):
        iid = yc.identifier(data.get('instance_id'), '实例 ID', r'[a-f0-9]{32}')
        kind = yc.choice(data.get('kind'), ('sealed', 'public'), '拍卖方式')
        pricing = yc.choice(data.get('pricing'), ('first', 'second'), '成交规则')
        start = yc.price_cents(data.get('start_price'))
        clock = yc.choice(data.get('clock', 'fixed'), ('fixed', 'rolling'), '计时规则')
        increment = 0
        duration = rolling = extension = 0
        if kind == 'sealed':
            if clock != 'fixed':
                raise yc.CardError('密封竞价仅支持固定持续时间')
            duration = hours(data.get('duration_hours'), '持续时间', 336)
        else:
            increment = yc.price_cents(data.get('increment'))
            if increment > start:
                raise yc.CardError('最小加价幅度不得大于起拍价')
            if clock == 'rolling':
                rolling = hours(data.get('rolling_hours'), '滚动截止时间', 24)
            else:
                duration = hours(data.get('duration_hours'), '持续时间', 336)
                extension = hours(data.get('extension_hours', 0), '延时时间', 12, zero=True)
        request_key = key(data.get('request_key'))
        digest = hashlib.sha256(yc.canonical([iid, kind, pricing, start, clock, increment, duration, rolling, extension]).encode()).hexdigest()
        self.settle_auctions()
        with self.transaction() as conn:
            old = conn.execute('SELECT id,request_hash FROM yc_auctions WHERE seller=? AND request_key=?', (user, request_key)).fetchone()
            if old:
                if old['request_hash'] != digest:
                    raise yc.CardError('同一请求编号不能用于不同拍卖', 409)
                return {'id': old['id']}
            self.available_card(conn, user, iid)
            if conn.execute("SELECT COUNT(*) FROM yc_auctions WHERE seller=? AND state='active'", (user,)).fetchone()[0] >= 10:
                raise yc.CardError('每个用户最多同时拍卖 10 张卡片，请等待现有拍卖结束', 409)
            if conn.execute("SELECT 1 FROM yc_listings WHERE instance=? AND state='active'", (iid,)).fetchone():
                raise yc.CardError('请先下架当前挂售，再创建拍卖', 409)
            if conn.execute("SELECT 1 FROM yc_auctions WHERE instance=? AND state='active'", (iid,)).fetchone():
                raise yc.CardError('该卡片正在拍卖', 409)
            aid, now = uuid.uuid4().hex, time.time()
            self.balance_change(conn, user, -1000, 'YCards 拍卖发布手续费', aid)
            conn.execute('''INSERT INTO yc_auctions(id,instance,seller,kind,pricing,clock,start_cents,
                increment_cents,duration_seconds,rolling_seconds,extension_seconds,created,ends,request_key,request_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (aid, iid, user, kind, pricing, clock, start, increment, duration, rolling, extension, now,
                 now+(rolling or duration), request_key, digest))
        return {'id': aid}

    def bid(self, user, data):
        aid = yc.identifier(data.get('auction_id'), '拍卖 ID', r'[a-f0-9]{32}')
        amount = yc.price_cents(data.get('amount'))
        request_key = key(data.get('request_key'))
        self.settle_auctions()
        with self.transaction() as conn:
            old = conn.execute('SELECT auction,amount_cents FROM yc_bid_events WHERE user=? AND request_key=?', (user, request_key)).fetchone()
            if old:
                if old['auction'] != aid or old['amount_cents'] != amount:
                    raise yc.CardError('同一请求编号不能用于不同出价', 409)
                return {'ok': True, 'auction_id': aid}
            a = conn.execute('SELECT a.*,i.owner FROM yc_auctions a JOIN yc_instances i ON i.id=a.instance WHERE a.id=?', (aid,)).fetchone()
            now = time.time()  # Read after acquiring the write lock.
            if not a or a['state'] != 'active' or now >= a['ends']:
                raise yc.CardError('拍卖已结束，请刷新查看结果', 409)
            if a['owner'] != a['seller']:
                raise yc.CardError('拍品归属已变化', 409)
            if a['seller'] == user:
                raise yc.CardError('不能竞拍自己的卡片', 403)
            mine = conn.execute('SELECT * FROM yc_bids WHERE auction=? AND user=?', (aid, user)).fetchone()
            top = conn.execute('SELECT * FROM yc_bids WHERE auction=? ORDER BY amount_cents DESC,priority LIMIT 1', (aid,)).fetchone()
            minimum = a['start_cents']
            if a['kind'] == 'public' and top:
                minimum = top['amount_cents'] + a['increment_cents']
            if amount < minimum:
                raise yc.CardError('报价低于起拍价或当前最低有效报价', 409)
            if mine and amount <= mine['amount_cents']:
                raise yc.CardError('再次出价必须高于自己的原报价', 409)
            held = amount + fee(amount)
            own_hold = mine['held_cents'] if mine else 0
            self.balance_change(conn, user, -(held-own_hold), 'YCards 拍卖冻结', aid)
            if a['kind'] == 'public':
                for b in conn.execute('SELECT user,held_cents FROM yc_bids WHERE auction=? AND user<>? AND held_cents>0', (aid, user)).fetchall():
                    self.balance_change(conn, b['user'], b['held_cents'], 'YCards 拍卖超价解冻', aid)
                    conn.execute('UPDATE yc_bids SET held_cents=0 WHERE auction=? AND user=?', (aid, b['user']))
            event = conn.execute('INSERT INTO yc_bid_events(auction,user,amount_cents,created,request_key) VALUES(?,?,?,?,?)',
                                 (aid, user, amount, now, request_key)).lastrowid
            conn.execute('''INSERT INTO yc_bids VALUES(?,?,?,?,?) ON CONFLICT(auction,user)
                            DO UPDATE SET amount_cents=excluded.amount_cents,held_cents=excluded.held_cents,priority=excluded.priority''',
                         (aid, user, amount, held, event))
            ends = a['ends']
            if a['kind'] == 'public':
                if a['clock'] == 'rolling':
                    ends = now + a['rolling_seconds']
                elif ends-now < a['extension_seconds']:
                    ends = now + a['extension_seconds']
            conn.execute('UPDATE yc_auctions SET ends=? WHERE id=?', (ends, aid))
        return {'ok': True, 'auction_id': aid}

    def settle_auctions(self, now=None):
        # Serialize with both bids and sales; a persisted auction can settle once.
        with self.transaction() as conn:
            now = time.time() if now is None else now
            auctions = conn.execute("SELECT a.*,i.owner FROM yc_auctions a JOIN yc_instances i ON i.id=a.instance WHERE a.state='active' AND a.ends<=? ORDER BY a.ends,a.id", (now,)).fetchall()
            for a in auctions:
                bids = conn.execute('SELECT * FROM yc_bids WHERE auction=? ORDER BY amount_cents DESC,priority', (a['id'],)).fetchall()
                # Recover defensively if an external administrator moved the card.
                cancelled = a['owner'] != a['seller']
                winner = bids[0] if bids and not cancelled else None
                price = None
                if winner:
                    price = winner['amount_cents'] if a['pricing'] == 'first' else max(a['start_cents'], bids[1]['amount_cents'] if len(bids)>1 else a['start_cents'])
                    if winner['held_cents'] < price+fee(price):
                        raise RuntimeError('Auction escrow invariant failed: '+a['id'])
                for b in bids:
                    old=conn.execute('SELECT * FROM yidt_escrow WHERE service=? AND reference=? AND user=?',('ycards',a['id'],b['user'])).fetchone()
                    held=int(old['units']) if old else b['held_cents']*y.CENT
                    refund=max(0,held-((price+fee(price))*y.CENT if winner and b['user']==winner['user'] else 0))
                    if old:y.escrow_set(conn,'ycards',a['id'],b['user'],old['address'],0)
                    if refund>0:y.service_credit(conn,b['user'],'ycards',refund,'YCards 拍卖结算退款',a['id'])
                conn.execute('UPDATE yc_bids SET held_cents=0 WHERE auction=?', (a['id'],))
                if winner:
                    self.balance_change(conn, a['seller'], price, 'YCards 拍卖卖出', a['id'])
                    conn.execute('UPDATE yc_instances SET owner=?,acquired=? WHERE id=?', (winner['user'], a['ends'], a['instance']))
                conn.execute('UPDATE yc_auctions SET state=?,winner=?,price_cents=?,fee_cents=?,finished=? WHERE id=?',
                             ('cancelled' if cancelled else 'sold' if winner else 'unsold', winner['user'] if winner else None,
                              price, fee(price) if price is not None else None, now, a['id']))

    def auction_view(self, conn, row, user, table=False):
        """Explicit allowlist: sealed responses never serialize bid identities.
        The price table is seller-only, including after settlement.
        """
        a = dict(row)
        bids = conn.execute('SELECT * FROM yc_bids WHERE auction=? ORDER BY amount_cents DESC,priority', (a['id'],)).fetchall()
        mine = next((b for b in bids if b['user'] == user), None)
        seller = user == a['seller']
        public = a['kind'] == 'public'
        show_price = public or seller
        result = {k: a[k] for k in ('id','instance','seller','kind','pricing','clock','start_cents','increment_cents',
                                    'duration_seconds','rolling_seconds','extension_seconds','created','ends','state','finished','cid','tid','name')}
        result.update(owned=seller, bid_count=len(bids),
                      current_cents=(bids[0]['amount_cents'] if bids else a['start_cents']) if show_price else None,
                      price_cents=a['price_cents'] if show_price or user == a['winner'] else None,
                      fee_cents=a['fee_cents'] if show_price or user == a['winner'] else None,
                      winner=a['winner'] if public or (seller and a['state']=='sold') else None,
                      won=bool(user and a['winner'] == user),
                      my_bid_cents=mine['amount_cents'] if mine else None,
                      held_cents=mine['held_cents'] if mine else 0,
                      leading=bool(public and mine and bids and bids[0]['user'] == user),
                      minimum_cents=(bids[0]['amount_cents']+a['increment_cents'] if public and bids else max(a['start_cents'],mine['amount_cents']+1 if mine else 0)))
        if table:
            if public:
                result['bids'] = [dict(user=b['user'],amount_cents=b['amount_cents'],created=b['created']) for b in conn.execute('SELECT user,amount_cents,created FROM yc_bid_events WHERE auction=? ORDER BY id DESC', (a['id'],))]
            elif seller:
                result['bids'] = [dict(amount_cents=b['amount_cents']) for b in bids]
            else:
                result['bids'] = None
        if public:
            import yidt_core as y
            receipts=list(conn.execute('SELECT hash,source FROM yidt_transactions WHERE reference=? ORDER BY id',(a['id'],)))
            result['yidt_transactions']=[r['hash'] for r in receipts]
            result['yidt_receipts']=[dict(hash=r['hash'],label='收款详情' if r['source']==y.OFFICIAL else '付款详情') for r in receipts]
        return result

    def auctions_list(self, user, args, history=False):
        self.settle_auctions()
        page = max(1, min(100000,int(args.get('page','1')) if str(args.get('page','1')).isdigit() else 1))
        section = args.get('section','active')
        where, params = ["a.state='active'"], []
        if history:
            yc.choice(section, ('active','sold','participated','ended'), '拍卖历史分类')
            if section == 'participated':
                where = ['EXISTS(SELECT 1 FROM yc_bids b WHERE b.auction=a.id AND b.user=?)']
            else:
                where = ['a.seller=?', "a.state='active'" if section == 'active' else "a.state='sold'" if section == 'sold' else "a.state IN ('unsold','cancelled')"]
            params.append(user)
        if args.get('name'):
            where.append('instr(COALESCE(n.name,t.name),?)>0');params.append(str(args['name'])[:100])
        if args.get('tid'):
            where.append('i.tid=?');params.append(str(args['tid'])[:12])
        base = ' FROM yc_auctions a JOIN yc_instances i ON i.id=a.instance JOIN yc_templates t ON t.tid=i.tid LEFT JOIN yc_card_names n ON n.instance=i.id WHERE '+' AND '.join(where)
        with self.connect() as conn:
            total = conn.execute('SELECT COUNT(*)'+base,params).fetchone()[0]
            page = min(page, max(1, (total+23)//24))
            rows = conn.execute('SELECT a.*,i.cid,i.tid,COALESCE(n.name,t.name) name'+base+' ORDER BY '+('a.created DESC' if history else 'a.ends,a.created')+' LIMIT 24 OFFSET ?',params+[(page-1)*24]).fetchall()
            items = [self.auction_view(conn,r,user) for r in rows]
        if args.get('compact') == '1':
            for item in items:
                item['card'] = self.preview(item['instance'], user, auction_id=item['id'], compact=True)
        return dict(items=items,total=total,page=page,now=time.time())

    def auction_detail(self, aid, user, compact=False, include_card=True):
        yc.identifier(aid, '拍卖 ID', r'[a-f0-9]{32}')
        self.settle_auctions()
        with self.connect() as conn:
            row = conn.execute('SELECT a.*,i.cid,i.tid,COALESCE(n.name,t.name) name FROM yc_auctions a JOIN yc_instances i ON i.id=a.instance JOIN yc_templates t ON t.tid=i.tid LEFT JOIN yc_card_names n ON n.instance=i.id WHERE a.id=?', (aid,)).fetchone()
            if not row:
                raise yc.CardError('拍卖不存在', 404)
            result = self.auction_view(conn,row,user,table=True)
        if include_card:
            result['card'] = self.preview(row['instance'],user,auction_id=aid,compact=compact)
        result['now'] = time.time()
        return result

    def auction_reserved(self, user):
        with self.connect() as conn:
            return conn.execute('SELECT COALESCE(SUM(held_cents),0) FROM yc_bids WHERE user=?', (user,)).fetchone()[0]
