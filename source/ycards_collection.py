"""Warehouse retirement and profile displays, sharing the existing points transaction."""
import time
import json
import ycards as yc

SCHEMA = '''
CREATE TABLE IF NOT EXISTS yc_destroyed(instance TEXT PRIMARY KEY REFERENCES yc_instances(id), user TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS yc_public_cards(instance TEXT PRIMARY KEY REFERENCES yc_instances(id), owner TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS yc_batch_list_requests(user TEXT NOT NULL, request_key TEXT NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(user,request_key));
CREATE TRIGGER IF NOT EXISTS yc_public_transfer AFTER UPDATE OF owner ON yc_instances WHEN NEW.owner != OLD.owner BEGIN
 DELETE FROM yc_public_cards WHERE instance=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS yc_public_destroy AFTER INSERT ON yc_destroyed BEGIN
 DELETE FROM yc_public_cards WHERE instance=NEW.instance;
END;


CREATE TABLE IF NOT EXISTS yc_showcase_purchases(user TEXT NOT NULL, slot INTEGER NOT NULL CHECK(slot BETWEEN 4 AND 6), request_key TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(user,slot), UNIQUE(user,request_key));
CREATE TABLE IF NOT EXISTS yc_showcase(user TEXT NOT NULL, slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 6), instance TEXT NOT NULL UNIQUE REFERENCES yc_instances(id), updated REAL NOT NULL, PRIMARY KEY(user,slot));
CREATE TRIGGER IF NOT EXISTS yc_showcase_transfer AFTER UPDATE OF owner ON yc_instances WHEN NEW.owner != OLD.owner BEGIN
 DELETE FROM yc_showcase WHERE instance=NEW.id;
END;
'''

class CollectionMixin:
    @staticmethod
    def available_card(conn, user, iid):
        row=conn.execute('SELECT owner FROM yc_instances WHERE id=? AND NOT EXISTS(SELECT 1 FROM yc_destroyed WHERE instance=yc_instances.id)',(iid,)).fetchone()
        if not row or row['owner'] != user:
            raise yc.CardError('卡片不存在、已损毁或不属于当前用户',403)
        return row

    def destroy_cards(self,user,data):
        if data.get('confirmed') is not True:
            raise yc.CardError('请再次确认损毁，操作不可撤销')
        ids=data.get('instance_ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=500:
            raise yc.CardError('每次请选择 1～500 张卡片')
        ids=list(dict.fromkeys(yc.identifier(i,'实例 ID',r'[a-f0-9]{32}') for i in ids))
        with self.transaction() as conn:
            pending=[]
            for iid in ids:
                old=conn.execute('SELECT user FROM yc_destroyed WHERE instance=?',(iid,)).fetchone()
                if old and old['user']==user:
                    continue # Safe retry; retain original tombstone and history.
                self.available_card(conn,user,iid)
                if conn.execute("SELECT 1 FROM yc_listings WHERE instance=? AND state='active'",(iid,)).fetchone():
                    raise yc.CardError('选择中有正在挂售的卡片，请先下架；本次未损毁任何卡片',409)
                if conn.execute("SELECT 1 FROM yc_auctions WHERE instance=? AND state='active'",(iid,)).fetchone():
                    raise yc.CardError('选择中有正在拍卖的卡片；本次未损毁任何卡片',409)
                if conn.execute('SELECT 1 FROM yc_showcase WHERE instance=?',(iid,)).fetchone():
                    raise yc.CardError('选择中有主页展示品，请先取消展示；本次未损毁任何卡片',409)
                pending.append(iid)
            conn.executemany('INSERT INTO yc_destroyed VALUES(?,?,?)',[(iid,user,time.time()) for iid in pending])
        return dict(ok=True,destroyed=len(pending))

    @staticmethod
    def showcase_slots(conn,user):
        return 3+conn.execute('SELECT COUNT(*) FROM yc_showcase_purchases WHERE user=?',(user,)).fetchone()[0]

    def showcase(self,user):
        with self.connect() as conn:
            slots=self.showcase_slots(conn,user)
            rows=conn.execute('SELECT s.slot,s.instance FROM yc_showcase s JOIN yc_instances i ON i.id=s.instance WHERE s.user=? AND i.owner=s.user AND NOT EXISTS(SELECT 1 FROM yc_destroyed d WHERE d.instance=i.id) ORDER BY s.slot',(user,)).fetchall()
        cards=[]
        for row in rows:
            try:
                card=self.preview(row['instance'],user,compact=True)
            except yc.CardError:
                continue # Concurrent transfer/removal; no private data survives.
            card['slot']=row['slot'];cards.append(card)
        return dict(slots=slots,cards=cards)

    def set_showcase(self,user,data):
        slot=yc.integer(data.get('slot'),'展示位置',1,6)
        iid=data.get('instance_id')
        if iid is not None:
            iid=yc.identifier(iid,'实例 ID',r'[a-f0-9]{32}')
        with self.transaction() as conn:
            if slot>self.showcase_slots(conn,user):
                raise yc.CardError('请先购买该展示框',409)
            if iid is not None:
                self.available_card(conn,user,iid)
                duplicate=conn.execute('SELECT slot FROM yc_showcase WHERE instance=? AND NOT(user=? AND slot=?)',(iid,user,slot)).fetchone()
                if duplicate:
                    raise yc.CardError('这张卡片已在其他位置展示',409)
            conn.execute('DELETE FROM yc_showcase WHERE user=? AND slot=?',(user,slot))
            if iid is not None:
                conn.execute('INSERT INTO yc_showcase VALUES(?,?,?,?)',(user,slot,iid,time.time()))
        return dict(ok=True)

    def buy_showcase_slot(self,user,data):
        key=yc.identifier(data.get('request_key'),'请求编号',r'[a-zA-Z0-9_-]{16,100}')
        expected=yc.integer(data.get('expected_slots'),'当前展示框数量',3,5)
        with self.transaction() as conn:
            old=conn.execute('SELECT slot FROM yc_showcase_purchases WHERE user=? AND request_key=?',(user,key)).fetchone()
            if old:
                if old['slot'] != expected+1:
                    raise yc.CardError('同一请求编号不能用于不同购买',409)
                return dict(ok=True,slots=self.showcase_slots(conn,user))
            slots=self.showcase_slots(conn,user)
            if slots>=6:
                raise yc.CardError('最多可拥有 6 个展示框',409)
            if slots!=expected:
                raise yc.CardError('展示框数量已变化，请刷新后重试',409)
            self.balance_change(conn,user,-1000000,'YCards 购买主页展示框',f'showcase:{user}:{slots+1}')
            conn.execute('INSERT INTO yc_showcase_purchases VALUES(?,?,?,?)',(user,slots+1,key,time.time()))
        return dict(ok=True,slots=slots+1)

    @staticmethod
    def selected_ids(data):
        ids=data.get('instance_ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=500:
            raise yc.CardError('每次请选择 1～500 张卡片')
        return sorted(set(yc.identifier(i,'实例 ID',r'[a-f0-9]{32}') for i in ids))

    def batch_list(self,user,data):
        ids=self.selected_ids(data)
        price=yc.price_cents(data.get('price'))
        key=yc.identifier(data.get('request_key'),'请求编号',r'[a-zA-Z0-9_-]{16,100}')
        payload=yc.canonical(dict(ids=ids,price=price))
        with self.transaction() as conn:
            old=conn.execute('SELECT payload,result FROM yc_batch_list_requests WHERE user=? AND request_key=?',(user,key)).fetchone()
            if old:
                if old['payload']!=payload:
                    raise yc.CardError('同一请求编号不能用于不同挂售',409)
                return json.loads(old['result'])
            for iid in ids:
                self.available_card(conn,user,iid)
                if conn.execute("SELECT 1 FROM yc_listings WHERE instance=? AND state='active'",(iid,)).fetchone() or conn.execute("SELECT 1 FROM yc_auctions WHERE instance=? AND state='active'",(iid,)).fetchone():
                    raise yc.CardError('选择中有正在挂售或拍卖的卡片，本次未发布任何挂单',409)
            now=time.time();listings=[]
            self.balance_change(conn,user,-1000*len(ids),'YCards 批量挂售发布手续费',key)
            for iid in ids:
                lid=yc.uuid.uuid4().hex
                conn.execute('INSERT INTO yc_listings VALUES(?,?,?,?,?,?,?)',(lid,iid,user,price,'active',now,now))
                import yidt_core as y
                conn.execute('INSERT OR IGNORE INTO yidt_routes VALUES(?,?,?,?)',('ycards',lid,user,y.bound_address(conn,user,'ycards')))
                listings.append(lid)
            result=dict(ok=True,count=len(ids),listing_ids=listings,fee_cents=len(ids)*1000)
            conn.execute('INSERT INTO yc_batch_list_requests VALUES(?,?,?,?,?)',(user,key,payload,yc.canonical(result),now))
        return result

    @staticmethod
    def public_url(row):
        return '/ycards/card/'+row['id']+'/'+row['cid']+'/'

    def public_card(self,iid,cid):
        yc.identifier(iid,'实例 ID',r'[a-f0-9]{32}')
        with self.connect() as conn:
            conn.execute('BEGIN') # Public permission and card data share one read snapshot.
            row=conn.execute('SELECT i.id,i.cid FROM yc_instances i JOIN yc_public_cards p ON p.instance=i.id AND p.owner=i.owner WHERE i.id=? AND i.cid=? AND NOT EXISTS(SELECT 1 FROM yc_destroyed WHERE instance=i.id)',(iid,cid)).fetchone()
            if not row:
                raise yc.CardError('此卡片未公开、已转手或已损毁',404)
            return self.preview(iid,compact=True,connection=conn)

    def set_public(self,user,data):
        ids=self.selected_ids(data)
        enabled=data.get('public',True)
        if not isinstance(enabled,bool):raise yc.CardError('公开状态无效')
        with self.transaction() as conn:
            for iid in ids:
                self.available_card(conn,user,iid)
            for iid in ids:
                conn.execute('DELETE FROM yc_public_cards WHERE instance=?',(iid,))
                if enabled:conn.execute('INSERT INTO yc_public_cards VALUES(?,?,?)',(iid,user,time.time()))
        return dict(ok=True,count=len(ids),public=enabled)

    def share_card(self,user,data):
        iid=yc.identifier(data.get('instance_id'),'实例 ID',r'[a-f0-9]{32}')
        # Sharing by viewers is allowed only while this exact owner's card is
        # actively offered. An ownership token makes transfer/share races reject.
        expected=data.get('expected_owner')
        with self.transaction() as conn:
            row=conn.execute('SELECT * FROM yc_instances WHERE id=? AND NOT EXISTS(SELECT 1 FROM yc_destroyed WHERE instance=yc_instances.id)',(iid,)).fetchone()
            if not row or expected!=row['owner']:
                raise yc.CardError('卡片归属已变化，请重新打开卡片',409)
            offered=conn.execute("SELECT 1 FROM yc_listings WHERE instance=? AND seller=? AND state='active'",(iid,row['owner'])).fetchone() or conn.execute("SELECT 1 FROM yc_auctions WHERE instance=? AND seller=? AND state='active'",(iid,row['owner'])).fetchone()
            if row['owner']!=user and not offered:
                raise yc.CardError('只能分享自己的卡片或当前市场上的卡片',403)
            conn.execute('INSERT OR IGNORE INTO yc_public_cards VALUES(?,?,?)',(iid,row['owner'],time.time()))
            return dict(url=self.public_url(row),cid=row['cid'],id=iid)
