"""YIDT adapter: same SQLite transaction as the authoritative, centralized ledger.
No simulated blockchain, signing key, remote broadcast, or client-supplied tx proof.
"""
import json, secrets, time, sys, re
from .db import connect, transaction
from .security import fail, digest
from . import membership
from . import policy

SCHEMA = '''
CREATE TABLE IF NOT EXISTS yichat_orders(
 id TEXT PRIMARY KEY, user TEXT NOT NULL, sid TEXT NOT NULL, room TEXT NOT NULL, kind TEXT NOT NULL,
 request_key TEXT NOT NULL, payload TEXT NOT NULL, source TEXT NOT NULL, recipient TEXT, destination TEXT,
 title TEXT NOT NULL, principal TEXT NOT NULL, remaining TEXT NOT NULL, count INTEGER NOT NULL,
 left_count INTEGER NOT NULL, fees TEXT NOT NULL, state TEXT NOT NULL, deposit TEXT UNIQUE,
 intent TEXT UNIQUE NOT NULL, created REAL NOT NULL, expires REAL, refund_principal TEXT NOT NULL DEFAULT '0',
 UNIQUE(user,request_key));
CREATE TABLE IF NOT EXISTS yichat_payouts(
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES yichat_orders(id), user TEXT NOT NULL,
 wallet TEXT NOT NULL, principal TEXT NOT NULL, gross TEXT NOT NULL, kind TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', tx TEXT UNIQUE, attempts INTEGER NOT NULL DEFAULT 0,
 retry_at REAL NOT NULL DEFAULT 0, error TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS yichat_one_claim_user ON yichat_payouts(order_id,user) WHERE kind='claim';
CREATE UNIQUE INDEX IF NOT EXISTS yichat_one_claim_wallet ON yichat_payouts(order_id,wallet) WHERE kind='claim';
CREATE UNIQUE INDEX IF NOT EXISTS yichat_one_refund ON yichat_payouts(order_id) WHERE kind='refund';
CREATE TABLE IF NOT EXISTS yichat_order_participants(order_id TEXT PRIMARY KEY REFERENCES yichat_orders(id),sender_uid TEXT NOT NULL,recipient_uid TEXT,request_key TEXT NOT NULL,UNIQUE(sender_uid,request_key));
CREATE TABLE IF NOT EXISTS yichat_payout_identities(payout_id TEXT PRIMARY KEY REFERENCES yichat_payouts(id),order_id TEXT NOT NULL,uid TEXT NOT NULL,kind TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS yichat_claim_uid ON yichat_payout_identities(order_id,uid) WHERE kind='claim';
CREATE TRIGGER IF NOT EXISTS yichat_frozen_order BEFORE UPDATE OF user,sid,room,kind,request_key,payload,source,recipient,destination,title,principal,count,fees,intent ON yichat_orders
BEGIN SELECT RAISE(ABORT,'order participants, wallets, principal and authorization are immutable'); END;
'''

def draw(remaining, count, minimum):
    if count<1 or remaining<count*minimum: raise ValueError('红包余额不合法')
    if count==1: return remaining
    hi=min(2*remaining//count,remaining-(count-1)*minimum)
    return minimum+secrets.randbelow(hi-minimum+1)

class Funds:
    def __init__(self, settings):
        self.s=settings
        sys.path.insert(0,str(settings.site))
        import yidt_core
        self.y=yidt_core
    def enabled(self):
        return self.s.funds_enabled and self.s.official==self.y.OFFICIAL
    def require(self):
        if not self.enabled(): fail(503,'资金功能未启用：需要配置已核实的官方钱包和显式启用开关')
    def migrate(self):
        with connect(self.s.ledger) as c: c.executescript(SCHEMA+membership.SCHEMA)
        with transaction(self.s.ledger) as c:
            for o in c.execute('SELECT * FROM yichat_orders WHERE id NOT IN (SELECT order_id FROM yichat_order_participants)').fetchall():
                c.execute('INSERT INTO yichat_order_participants VALUES(?,?,?,?)',(o['id'],policy.historical_uid(self.s,o['user']),policy.historical_uid(self.s,o['recipient']) if o['recipient'] else None,o['request_key']))
            for p in c.execute('SELECT * FROM yichat_payouts WHERE id NOT IN (SELECT payout_id FROM yichat_payout_identities)').fetchall():
                c.execute('INSERT INTO yichat_payout_identities VALUES(?,?,?,?)',(p['id'],p['order_id'],policy.historical_uid(self.s,p['user']),p['kind']))
    def binding(self,c,user):
        b=self.y.binding(c,user,'yichat')
        if not b: fail(409,'请先在 YIDT 绑定 YiChat 钱包')
        return b
    def create(self,s,room,data,recipient=None,native_return=False):
        self.require(); y=self.y
        key=str(data.get('key',''))
        if not re.fullmatch('[A-Za-z0-9_-]{16,100}',key): fail(400,'需要有效的幂等键')
        kind=room['kind']; count=data.get('count',1) if kind=='group' else 1
        if type(count)!=int or not 1<=count<=1000: fail(400,'红包份数应为 1～1000')
        amount=str(data.get('amount',''))
        if len(amount)>100 or not re.fullmatch(r'\d+(?:\.\d{1,16})?',amount): fail(400,'金额最多支持 16 位小数')
        principal=y.units(amount)
        if principal<count*y.CENT: fail(400,'每份至少 0.01 YIDT')
        title=str(data.get('title','私聊转账' if kind=='direct' else '恭喜发财'))[:100]
        recipient_uid=policy.identity(self.s,recipient)['uid'] if recipient else None
        payload=y.canonical([room['room'],kind,amount,count,title,recipient_uid])
        with transaction(self.s.ledger) as c:
            if not y.sid_valid(c,s['sid'],s['username']): fail(401,'主站已退出')
            membership.require(c,room['room'],s['mxid'])
            if kind=='direct':policy.require_uid_send(self.s,s['subject'],recipient_uid)
            old=c.execute('SELECT o.* FROM yichat_orders o JOIN yichat_order_participants p ON p.order_id=o.id WHERE p.sender_uid=? AND p.request_key=?',(s['subject'],key)).fetchone()
            if old:
                if old['payload']!=payload: fail(409,'幂等键已用于不同订单')
                return self.public(c,old,s['username'])
            source=self.binding(c,s['username'])
            dest=self.binding(c,recipient)['address'] if recipient else None
            if dest==source['address'] or source['address']==self.s.official: fail(400,'发送和接收钱包不能相同')
            fees=count*y.GAS; gross=principal+fees
            # Unlike legacy quote's all-in fallback, never reduce principal for fees.
            if int(y.wallet(c,source['address'])['balance'])<gross+y.GAS: fail(409,'余额不足以支付本金及独立手续费')
            oid=secrets.token_urlsafe(24); intent=secrets.token_urlsafe(32); now=time.time()
            path=f'/api/orders/{oid}/fund'; body={}
            reason=f'YiChat 本金 {y.fmt(principal)}；付款钱包 {source["address"]}；收款钱包 {dest or "群成员领取时验证绑定钱包"}；出金手续费预留 {y.fmt(fees)}；入金 gas {y.fmt(y.GAS)}'
            return_url=self.s.origin+('/app/return?order=' if native_return else '/orders/')+oid
            c.execute('INSERT INTO yidt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(intent,s['username'],s['sid'],'yichat',y.payload_hash(path,body),str(gross),reason,return_url,source['address'],'pending',now,now+900,None,source['created']))
            c.execute('INSERT INTO yichat_orders(id,user,sid,room,kind,request_key,payload,source,recipient,destination,title,principal,remaining,count,left_count,fees,state,intent,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(oid,s['username'],s['sid'],room['room'],kind,key,payload,source['address'],recipient,dest,title,str(principal),str(principal),count,count,str(fees),'awaiting_deposit',intent,now))
            c.execute('INSERT INTO yichat_order_participants VALUES(?,?,?,?)',(oid,s['subject'],recipient_uid,key))
            return self.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone(),s['username'])
    def cancel(self,s,oid):
        """Abandon an unfunded order under the same transaction lock as fund."""
        with transaction(self.s.ledger) as c:
            o=c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone()
            participant=c.execute('SELECT * FROM yichat_order_participants WHERE order_id=?',(oid,)).fetchone()
            if not o or not participant or participant['sender_uid']!=s['subject']: fail(403,'只有付款人可以取消订单')
            if not self.y.sid_valid(c,s['sid'],s['username']): fail(401,'主站已退出')
            membership.require(c,o['room'],s['mxid'])
            if o['state']=='cancelled': return self.public(c,o,s['username'])
            if o['state']!='awaiting_deposit' or o['deposit']: fail(409,'订单已入金，不能取消；请按收款、领取或到期退款流程处理')
            c.execute("UPDATE yidt_intents SET state='cancelled' WHERE id=? AND state IN ('pending','approved')",(o['intent'],))
            c.execute("UPDATE yichat_orders SET state='cancelled',remaining='0',left_count=0 WHERE id=?",(oid,))
            return self.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone(),s['username'])
    def fund(self,s,oid):
        self.require(); y=self.y
        with transaction(self.s.ledger) as c:
            o=c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone()
            participant=c.execute('SELECT * FROM yichat_order_participants WHERE order_id=?',(oid,)).fetchone()
            if not o or not participant or participant['sender_uid']!=s['subject'] or o['sid']!=s['sid']: fail(403,'订单不属于当前付款会话')
            if not y.sid_valid(c,s['sid'],s['username']): fail(401,'主站已退出')
            membership.require(c,o['room'],s['mxid'])
            if o['state']=='cancelled': fail(409,'订单已取消，不再入金或发送')
            if o['state']!='awaiting_deposit': return self.public(c,o,s['username'])
            if o['kind']=='direct':policy.require_uid_send(self.s,participant['sender_uid'],participant['recipient_uid'])
            intent=c.execute('SELECT * FROM yidt_intents WHERE id=?',(o['intent'],)).fetchone(); b=self.binding(c,s['username'])
            gross=int(o['principal'])+int(o['fees'])
            if not intent or intent['state']!='approved' or intent['expires']<=time.time() or intent['address']!=b['address'] or intent['binding_stamp']!=b['created'] or b['address']!=o['source'] or int(intent['amount'])!=gross or intent['payload_hash']!=y.payload_hash(f'/api/orders/{oid}/fund',{}): fail(409,'请先在 YIDT 确认此订单；绑定变更后需重新创建')
            if int(y.wallet(c,o['source'])['balance'])<gross+y.GAS: fail(409,'余额不足以支付本金及手续费')
            tx=y.transfer(c,o['source'],self.s.official,gross,'yichat_deposit',oid,'YiChat 入金')
            if y.units(tx['credit'])!=gross: raise RuntimeError('Unexpected credit')
            c.execute("UPDATE yidt_intents SET state='spent',tx=? WHERE id=?",(tx['hash'],o['intent']))
            y.escrow_set(c,'yichat',oid,o['user'],o['source'],gross)
            direct=o['kind']=='direct'
            c.execute("UPDATE yichat_orders SET state=?,deposit=?,expires=? WHERE id=?",('awaiting_receipt' if direct else 'active',tx['hash'],time.time()+(self.s.transfer_ttl if direct else self.s.packet_ttl),oid))
            return self.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone(),s['username'])
    def receive(self,s,oid,room):
        """Only the designated recipient can atomically authorize the frozen payout."""
        self.require()
        with transaction(self.s.ledger) as c:
            if not self.y.sid_valid(c,s['sid'],s['username']): fail(401,'主站已退出')
            o=c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone()
            participant=c.execute('SELECT * FROM yichat_order_participants WHERE order_id=?',(oid,)).fetchone()
            if not o or not participant or o['kind']!='direct' or o['room']!=room or participant['recipient_uid']!=s['subject'] or participant['sender_uid']==s['subject']:
                fail(403,'只有此私聊转账的指定收款人可以确认收款')
            membership.require(c,room,s['mxid'])
            # Acknowledged requests stay idempotent after payout, expiry, or wallet changes.
            if c.execute("SELECT 1 FROM yichat_payouts WHERE order_id=? AND kind='claim' AND user=?",(oid,s['username'])).fetchone():
                return self.public(c,o,s['username'])
            if o['state']!='awaiting_receipt' or o['expires']<=time.time(): fail(409,'转账尚未入金、已到期或已退款')
            if self.binding(c,s['username'])['address']!=o['destination']:
                fail(409,'绑定钱包已变更；请恢复订单指定钱包后收款，或等待到期退款')
            self.enqueue(c,o,s['username'],o['destination'],int(o['principal']),'claim')
            c.execute("UPDATE yichat_orders SET remaining='0',left_count=0,state='paying' WHERE id=?",(oid,))
            return self.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone(),s['username'])
    def enqueue(self,c,o,user,wallet,n,kind,gross=None):
        pid=secrets.token_urlsafe(24)
        c.execute('INSERT INTO yichat_payouts(id,order_id,user,wallet,principal,gross,kind) VALUES(?,?,?,?,?,?,?)',(pid,o['id'],user,wallet,str(n),str(gross if gross is not None else n+self.y.GAS),kind))
        uid=policy.identity(self.s,user)['uid'] if kind=='claim' else c.execute('SELECT sender_uid FROM yichat_order_participants WHERE order_id=?',(o['id'],)).fetchone()[0]
        c.execute('INSERT INTO yichat_payout_identities VALUES(?,?,?,?)',(pid,o['id'],uid,kind))
    def claim(self,s,oid,room):
        self.require(); y=self.y
        with transaction(self.s.ledger) as c:
            if not y.sid_valid(c,s['sid'],s['username']): fail(401,'主站已退出')
            o=c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone()
            if not o or o['kind']!='group' or o['room']!=room: fail(403,'此红包不属于当前群聊')
            membership.require(c,room,s['mxid'])
            participant=c.execute('SELECT * FROM yichat_order_participants WHERE order_id=?',(oid,)).fetchone()
            if not participant or participant['sender_uid']==s['subject']: fail(403,'不能领取自己的红包')
            b=self.binding(c,s['username'])
            if b['address']==o['source']: fail(403,'发送钱包不能领取')
            old=c.execute("SELECT p.*,i.uid claim_uid FROM yichat_payouts p JOIN yichat_payout_identities i ON p.id=i.payout_id WHERE p.order_id=? AND p.kind='claim' AND (i.uid=? OR p.wallet=?)",(oid,s['subject'],b['address'])).fetchone()
            if old:
                if old['claim_uid']!=s['subject']: fail(409,'此钱包已领取')
                return self.public(c,o,s['username'])
            if o['state']!='active' or o['expires']<=time.time() or o['left_count']==0: fail(409,'红包不可领取或已到期')
            n=draw(int(o['remaining']),o['left_count'],y.CENT)
            self.enqueue(c,o,s['username'],b['address'],n,'claim')
            c.execute("UPDATE yichat_orders SET remaining=?,left_count=left_count-1,state=CASE WHEN left_count=1 THEN 'paying' ELSE state END WHERE id=?",(str(int(o['remaining'])-n),oid))
            return self.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone(),s['username'])
    def expire(self):
        if not self.enabled(): return
        y=self.y
        with transaction(self.s.ledger) as c:
            for o in c.execute("SELECT * FROM yichat_orders WHERE state IN ('active','awaiting_receipt') AND expires<=?",(time.time(),)).fetchall():
                pending=sum(int(p[0]) for p in c.execute("SELECT gross FROM yichat_payouts WHERE order_id=? AND state='pending'",(o['id'],)))
                reserve=c.execute("SELECT units FROM yidt_escrow WHERE service='yichat' AND reference=? AND user=?",(o['id'],o['user'])).fetchone()
                gross=int(reserve[0])-pending
                if gross<int(o['remaining'])+y.GAS: raise RuntimeError('Refund reserve mismatch')
                self.enqueue(c,o,o['user'],o['source'],int(o['remaining']),'refund',gross)
                c.execute("UPDATE yichat_orders SET state='refunding',refund_principal=remaining,remaining='0',left_count=0 WHERE id=?",(o['id'],))
    def settle_one(self,pid,after_transfer=None):
        """Ledger write and outbox acknowledgement commit together; timeout/restart cannot double pay."""
        y=self.y
        with transaction(self.s.ledger) as c:
            p=c.execute('SELECT * FROM yichat_payouts WHERE id=?',(pid,)).fetchone()
            if not p or p['state']!='pending': return
            o=c.execute('SELECT * FROM yichat_orders WHERE id=?',(p['order_id'],)).fetchone()
            reserve=c.execute("SELECT units FROM yidt_escrow WHERE service='yichat' AND reference=? AND user=?",(o['id'],o['user'])).fetchone()
            left=int(reserve[0])-int(p['gross'])
            if left<0: raise RuntimeError('Escrow invariant')
            y.escrow_set(c,'yichat',o['id'],o['user'],o['source'],left)
            # Check authoritative ledger first on every retry; references are server-generated.
            tx=c.execute("SELECT hash FROM yidt_transactions WHERE kind='yichat_payout' AND reference=?",(pid,)).fetchone()
            if tx: raise RuntimeError('Ledger/outbox divergence: manual reconciliation required')
            result=y.transfer(c,self.s.official,p['wallet'],int(p['gross']),'yichat_payout',pid,'YiChat '+p['kind'])
            if y.units(result['credit'])!=int(p['gross'])-y.GAS: raise RuntimeError('Payout mismatch')
            if after_transfer: after_transfer()
            c.execute("UPDATE yichat_payouts SET state='paid',tx=?,error=NULL WHERE id=?",(result['hash'],pid))
            if int(o['remaining'])==0 and not c.execute("SELECT 1 FROM yichat_payouts WHERE order_id=? AND state='pending'",(o['id'],)).fetchone():
                c.execute('UPDATE yichat_orders SET state=? WHERE id=?',('refunded' if o['state']=='refunding' else 'paid',o['id']))
    def tick(self):
        if not self.enabled(): return
        self.reconcile()
        self.expire()
        with connect(self.s.ledger) as c: rows=c.execute("SELECT id FROM yichat_payouts WHERE state='pending' AND retry_at<=? LIMIT 25",(time.time(),)).fetchall()
        for row in rows:
            try: self.settle_one(row['id'])
            except Exception:
                with transaction(self.s.ledger) as c: c.execute("UPDATE yichat_payouts SET attempts=attempts+1,retry_at=?,error='账本出金失败；等待重试或对账' WHERE id=? AND state='pending'",(time.time()+30,row['id']))
    def public(self,c,o,viewer):
        payouts=c.execute('SELECT p.user,p.principal,p.state,p.tx,p.kind,i.uid user_id FROM yichat_payouts p JOIN yichat_payout_identities i ON i.payout_id=p.id WHERE p.order_id=? ORDER BY p.rowid',(o['id'],)).fetchall()
        claims=[dict(p)|{'amount':self.y.fmt(p['principal'])} for p in payouts if p['kind']=='claim']
        participants=c.execute('SELECT sender_uid,recipient_uid FROM yichat_order_participants WHERE order_id=?',(o['id'],)).fetchone()
        viewer_uid=policy.identity(self.s,viewer)['uid']
        recipient_mxid=None
        if participants['recipient_uid']:
            with connect(self.s.db) as identities:
                recipient=identities.execute('SELECT mxid FROM identities WHERE subject=?',(participants['recipient_uid'],)).fetchone()
            recipient_mxid=recipient['mxid'] if recipient else '@u_'+digest(participants['recipient_uid'])+':'+self.s.server_name
        actions=dict(fund=self.enabled() and viewer_uid==participants['sender_uid'] and o['state']=='awaiting_deposit',receive=self.enabled() and viewer_uid==participants['recipient_uid'] and o['state']=='awaiting_receipt' and o['expires']>time.time(),claim=self.enabled() and o['kind']=='group' and viewer_uid!=participants['sender_uid'] and o['state']=='active' and o['expires']>time.time() and not any(p['user_id']==viewer_uid for p in claims))
        actions['cancel']=viewer_uid==participants['sender_uid'] and o['state']=='awaiting_deposit' and not o['deposit']
        return dict(recipient_mxid=recipient_mxid,actions=actions,sender_uid=participants['sender_uid'],recipient_uid=participants['recipient_uid'],id=o['id'],room=o['room'],kind=o['kind'],title=o['title'],sender=o['user'],recipient=o['recipient'],destination=o['destination'],source=o['source'],amount=self.y.fmt(o['principal']),count=o['count'],state=('expired' if o['state'] in ('active','awaiting_receipt') and o['expires']<=time.time() else o['state']),expires=o['expires'],receipt_ttl_seconds=self.s.transfer_ttl if o['kind']=='direct' else None,deposit=o['deposit'],claims=claims,refund=next(({'principal':str(p['principal']),'amount':self.y.fmt(p['principal']),'state':p['state'],'tx':p['tx']} for p in payouts if p['kind']=='refund'),None),best=max(claims,key=lambda x:int(x['principal']))['user'] if claims else None,my_amount=next((p['amount'] for p in claims if p['user_id']==viewer_uid),None),incoming_fee=self.y.fmt(self.y.GAS),outgoing_fee_reserve=self.y.fmt(int(o['principal']) and o['count']*self.y.GAS),authorize_url=self.y.ORIGIN+'/authorize/'+o['intent'] if actions['fund'] else None,url=self.s.origin+'/orders/'+o['id'])
    def reconcile(self):
        y=self.y; result=[]
        with connect(self.s.ledger) as c:
            y.verify_chain(c)
            for o in c.execute("SELECT * FROM yichat_orders WHERE deposit IS NOT NULL"):
                claims=sum(int(p[0]) for p in c.execute("SELECT principal FROM yichat_payouts WHERE order_id=? AND kind='claim'",(o['id'],)))
                pending=sum(int(p[0]) for p in c.execute("SELECT gross FROM yichat_payouts WHERE order_id=? AND state='pending'",(o['id'],)))
                actual=int(c.execute("SELECT units FROM yidt_escrow WHERE service='yichat' AND reference=? AND user=?",(o['id'],o['user'])).fetchone()[0])
                unallocated=int(o['remaining'])+(o['left_count']*y.GAS if o['state'] in ('active','awaiting_receipt') else 0)
                ok=claims+int(o['remaining'])+int(o['refund_principal'])==int(o['principal']) and actual==pending+unallocated
                deposit=c.execute('SELECT * FROM yidt_transactions WHERE hash=?',(o['deposit'],)).fetchone()
                ok=ok and bool(deposit and deposit['source']==o['source'] and deposit['destination']==self.s.official and deposit['kind']=='yichat_deposit' and deposit['reference']==o['id'] and int(deposit['credit'])==int(o['principal'])+int(o['fees']))
                for payout in c.execute("SELECT * FROM yichat_payouts WHERE order_id=? AND state='paid'",(o['id'],)):
                    tx=c.execute('SELECT * FROM yidt_transactions WHERE hash=?',(payout['tx'],)).fetchone()
                    ok=ok and bool(tx and tx['source']==self.s.official and tx['destination']==payout['wallet'] and tx['kind']=='yichat_payout' and tx['reference']==payout['id'] and int(tx['credit'])==int(payout['gross'])-y.GAS)
                if not ok: raise RuntimeError('YiChat reconciliation failed: '+o['id'])
                result.append({'id':o['id'],'ok':True,'escrow':str(actual)})
        return result
