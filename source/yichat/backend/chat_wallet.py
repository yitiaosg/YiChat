"""Custodial chat balances. All amounts use YIDT's integer 10^-16 units.
Legacy chain-funded orders continue through Funds without changing their terms.
"""
import hashlib,json,re,secrets,time
from .funds import Funds as LegacyFunds,draw
from .db import connect,transaction
from .security import fail
from . import policy,membership
SCHEMA='''
CREATE TABLE IF NOT EXISTS chat_wallet_accounts(uid TEXT PRIMARY KEY,balance TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_wallet_journal(id INTEGER PRIMARY KEY,reference TEXT NOT NULL,source TEXT,destination TEXT,amount TEXT NOT NULL,created REAL NOT NULL,previous TEXT NOT NULL,hash TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS chat_wallet_operations(id TEXT PRIMARY KEY,uid TEXT NOT NULL,request_key TEXT NOT NULL,kind TEXT NOT NULL,amount TEXT NOT NULL,address TEXT NOT NULL,binding_stamp TEXT NOT NULL,intent TEXT,tx TEXT,created REAL NOT NULL,UNIQUE(uid,request_key));
'''
class Funds(LegacyFunds):
    def migrate(self):
        super().migrate()
        with connect(self.s.ledger) as c:c.executescript(SCHEMA)
    def bank(self,c):
        row=c.execute("SELECT value FROM yidt_meta WHERE key='yichat_wallet'").fetchone()
        if row:return row[0]
        y=self.y;mn=y.Mnemonic('english');words=mn.generate(128)
        address=y.new_wallet(c,words,hashlib.sha256(mn.to_seed(words)+b'YIDT wallet v1').hexdigest())
        tx=y.transfer(c,y.OFFICIAL,address,1000*y.SCALE+y.GAS,'pool_funding','yichat:initial','YiChat 官方钱包初始资金')
        if y.units(tx['credit'])!=1000*y.SCALE:raise RuntimeError('Initial funding mismatch')
        c.executemany('INSERT INTO yidt_meta VALUES(?,?)',[('yichat_wallet',address),('yichat_initial_tx',tx['hash'])])
        return address
    def balance(self,c,uid):
        row=c.execute('SELECT balance FROM chat_wallet_accounts WHERE uid=?',(uid,)).fetchone()
        return int(row[0]) if row else 0
    def move(self,c,source,dest,amount,reference):
        if amount<=0:raise ValueError('Nonpositive wallet posting')
        if source and self.balance(c,source)<amount:fail(409,{'code':'YICHAT_INSUFFICIENT_BALANCE','error':'YiChat 钱包余额不足，请前往 YIDT 钱包充值','wallet_url':self.y.ORIGIN+'/services/yichat/'})
        for uid,delta in ((source,-amount),(dest,amount)):
            if uid:c.execute('INSERT INTO chat_wallet_accounts VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET balance=excluded.balance',(uid,str(self.balance(c,uid)+delta)))
        row=c.execute('SELECT hash FROM chat_wallet_journal ORDER BY id DESC LIMIT 1').fetchone();previous=row[0] if row else '0'*64;now=time.time()
        digest=hashlib.sha256(self.y.canonical([reference,source,dest,str(amount),now,previous]).encode()).hexdigest()
        c.execute('INSERT INTO chat_wallet_journal(reference,source,destination,amount,created,previous,hash) VALUES(?,?,?,?,?,?,?)',(reference,source,dest,str(amount),now,previous,digest))
    def auth(self,c,who):
        if who.get('key_id'):
            from .open_api import owner
            with connect(self.s.db) as db:row=db.execute('SELECT scope,subject FROM api_keys WHERE id=?',(who['key_id'],)).fetchone()
            if not row or row['scope']!='read_write' or row['subject']!=who['subject']:fail(401,'API Key 已失效')
        elif not self.y.sid_valid(c,who['sid'],who['username']):fail(401,'主站已退出')
    def amount(self,data):
        value=str(data.get('amount',''))
        if not re.fullmatch(r'\d+(?:\.\d{1,16})?',value) or len(value)>100:fail(400,'金额最多支持16位小数')
        n=self.y.units(value)
        if n<self.y.CENT:fail(400,'金额至少0.01 YIDT')
        key=data.get('key','')
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{16,100}',key):fail(400,'需要16～100字符幂等键')
        return n,key
    def internal(self,oid):
        with connect(self.s.ledger) as c:r=c.execute('SELECT deposit FROM yichat_orders WHERE id=?',(oid,)).fetchone()
        return bool(r and (r[0] or '').startswith('internal:'))
    def create(self,who,room,data,recipient=None,native_return=None):
        self.require();n,key=self.amount(data);count=data.get('count',1) if room['kind']=='group' else 1
        if type(count)!=int or not 1<=count<=1000 or n<count*self.y.CENT:fail(400,'每份红包至少0.01，份数1～1000')
        title=data.get('title','私聊转账' if room['kind']=='direct' else '恭喜发财')
        if not isinstance(title,str) or len(title)>100:fail(400,'标题最多100字符')
        recipient_uid=policy.identity(self.s,recipient)['uid'] if recipient else None
        payload=self.y.canonical([room['room'],room['kind'],str(n),count,title,recipient_uid])
        with transaction(self.s.ledger) as c:
            self.auth(c,who)
            from .lifecycle import require_active
            require_active(c,room['room']);membership.require(c,room['room'],who['mxid'])
            if recipient_uid:policy.require_uid_send(self.s,who['subject'],recipient_uid)
            if room['kind']=='group' and count>c.execute("SELECT count(*) FROM yichat_memberships WHERE room=? AND membership='join' AND pending_event IS NULL",(room['room'],)).fetchone()[0]:fail(400,'红包份数超过群人数')
            old=c.execute('SELECT o.* FROM yichat_orders o JOIN yichat_order_participants p ON o.id=p.order_id WHERE p.sender_uid=? AND p.request_key=?',(who['subject'],key)).fetchone()
            if old:
                if old['payload']!=payload:fail(409,'幂等键已用于不同订单')
                return self.public(c,old,who['username'])
            oid=secrets.token_urlsafe(24);now=time.time();self.move(c,who['subject'],'order:'+oid,n,'reserve:'+oid)
            c.execute('INSERT INTO yichat_orders(id,user,sid,room,kind,request_key,payload,source,recipient,destination,title,principal,remaining,count,left_count,fees,state,deposit,intent,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(oid,who['username'],who.get('sid','api:'+who.get('key_id','')),room['room'],room['kind'],key,payload,who['subject'],recipient,recipient_uid,title,str(n),str(n),count,count,'0','awaiting_receipt' if recipient else 'active','internal:'+oid,'wallet:'+oid,now,now+(self.s.transfer_ttl if recipient else self.s.packet_ttl)))
            c.execute('INSERT INTO yichat_order_participants VALUES(?,?,?,?)',(oid,who['subject'],recipient_uid,key))
            return self.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone(),who['username'])
    def fund(self,who,oid):
        if not self.internal(oid):return super().fund(who,oid)
        with connect(self.s.ledger) as c:return self.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone(),who['username'])
    def receive(self,who,oid,room):
        if not self.internal(oid):return super().receive(who,oid,room)
        return self.take(who,oid,room,False)
    def claim(self,who,oid,room):
        if not self.internal(oid):return super().claim(who,oid,room)
        return self.take(who,oid,room,True)
    def take(self,who,oid,room,group):
        self.require()
        with transaction(self.s.ledger) as c:
            self.auth(c,who)
            from .lifecycle import require_active
            require_active(c,room);membership.require(c,room,who['mxid'])
            o=c.execute('SELECT * FROM yichat_orders WHERE id=? AND room=?',(oid,room)).fetchone()
            if not o or (o['kind']=='group')!=group or who['subject']==o['source'] or (not group and who['subject']!=o['destination']):fail(403,'不是此订单的合法收款人')
            if c.execute("SELECT 1 FROM yichat_payout_identities WHERE order_id=? AND uid=? AND kind='claim'",(oid,who['subject'])).fetchone():return self.public(c,o,who['username'])
            if o['expires']<=time.time() or o['state'] not in ('active','awaiting_receipt'):fail(409,'订单已结束或到期')
            n=draw(int(o['remaining']),o['left_count'],self.y.CENT) if group else int(o['principal']);pid=secrets.token_urlsafe(24)
            self.move(c,'order:'+oid,who['subject'],n,'claim:'+pid)
            c.execute("INSERT INTO yichat_payouts(id,order_id,user,wallet,principal,gross,kind,state,tx) VALUES(?,?,?,?,?,?,'claim','paid',?)",(pid,oid,who['username'],who['subject'],str(n),str(n),'internal:'+pid))
            c.execute("INSERT INTO yichat_payout_identities VALUES(?,?,?,'claim')",(pid,oid,who['subject']))
            c.execute("UPDATE yichat_orders SET remaining=?,left_count=left_count-1,state=CASE WHEN left_count=1 THEN 'paid' ELSE state END WHERE id=?",(str(int(o['remaining'])-n),oid))
            return self.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone(),who['username'])
    def expire(self):
        super().expire()
        with transaction(self.s.ledger) as c:
            for o in c.execute("SELECT * FROM yichat_orders WHERE deposit LIKE 'internal:%' AND state IN ('active','awaiting_receipt') AND expires<=?",(time.time(),)).fetchall():
                n=int(o['remaining']);self.move(c,'order:'+o['id'],o['source'],n,'refund:'+o['id']);pid=secrets.token_urlsafe(24)
                c.execute("INSERT INTO yichat_payouts(id,order_id,user,wallet,principal,gross,kind,state,tx) VALUES(?,?,?,?,?,?,'refund','paid',?)",(pid,o['id'],o['user'],o['source'],str(n),str(n),'internal:'+pid))
                c.execute("INSERT INTO yichat_payout_identities VALUES(?,?,?,'refund')",(pid,o['id'],o['source']))
                c.execute("UPDATE yichat_orders SET refund_principal=remaining,remaining='0',left_count=0,state='refunded' WHERE id=?",(o['id'],))
    def public(self,c,o,viewer):
        result=super().public(c,o,viewer)
        if (o['deposit'] or '').startswith('internal:'):result.update(internal=True,incoming_fee='0',outgoing_fee_reserve='0',authorize_url=None)
        return result
    def reconcile(self):
        result=super().reconcile();balances={};prev='0'*64
        with connect(self.s.ledger) as c:
            for r in c.execute('SELECT * FROM chat_wallet_journal ORDER BY id'):
                h=hashlib.sha256(self.y.canonical([r['reference'],r['source'],r['destination'],r['amount'],r['created'],prev]).encode()).hexdigest()
                if r['previous']!=prev or r['hash']!=h:raise RuntimeError('Chat wallet journal integrity')
                for uid,delta in ((r['source'],-int(r['amount'])),(r['destination'],int(r['amount']))):
                    if uid:balances[uid]=balances.get(uid,0)+delta
                prev=h
            stored={r['uid']:int(r['balance']) for r in c.execute('SELECT * FROM chat_wallet_accounts')}
            if stored!=balances or any(n<0 for n in stored.values()):raise RuntimeError('Chat wallet balance mismatch')
            bank=c.execute("SELECT value FROM yidt_meta WHERE key='yichat_wallet'").fetchone()
            if balances and (not bank or int(self.y.wallet(c,bank[0])['balance'])<sum(balances.values())):raise RuntimeError('Chat wallet backing deficit')
            for o in c.execute("SELECT id,remaining FROM yichat_orders WHERE deposit LIKE 'internal:%'"):
                if self.balance(c,'order:'+o['id'])!=int(o['remaining']):raise RuntimeError('Chat wallet reserve mismatch')
        return result
    def wallet_state(self,who):
        with connect(self.s.ledger) as c:
            b=self.y.binding(c,who['username'],'yichat');balance=self.balance(c,who['subject'])
        return {'enabled':self.enabled(),'binding':b['address'] if b else None,'balance':self.y.fmt(balance),'bind_url':self.y.ORIGIN+'/services/yichat/','fees':{'incoming':'0','outgoing_per_share':'0','wallet':'0.5','gas':self.y.fmt(self.y.GAS)}}
    def payment(self,who,data,kind,native=False):
        self.require();n,key=self.amount(data);fee=self.y.SCALE//2;y=self.y
        with transaction(self.s.ledger) as c:
            self.auth(c,who);b=self.binding(c,who['username']);bank=self.bank(c)
            old=c.execute('SELECT * FROM chat_wallet_operations WHERE uid=? AND request_key=?',(who['subject'],key)).fetchone()
            if old:
                if int(old['amount'])!=n or old['kind']!=kind:fail(409,'幂等键已用于不同操作')
                return self.operation(old)
            oid=secrets.token_urlsafe(24);intent=secrets.token_urlsafe(32) if kind=='deposit' else None
            if kind=='deposit':
                path='/api/wallet/deposits/'+oid+'/fund'
                c.execute('INSERT INTO yidt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(intent,who['username'],who['sid'],'yichat',y.payload_hash(path,{}),str(n+fee),'YiChat 钱包充值 '+y.fmt(n)+' YIDT；手续费 0.5 YIDT，另付 gas',self.s.origin+('/app/return?wallet_deposit=' if native else '/?wallet_deposit=')+oid,b['address'],'pending',time.time(),time.time()+900,None,b['created']))
                tx=None
            else:
                if int(y.wallet(c,bank)['balance'])<n+y.GAS:fail(503,'钱包出金余额暂不足，请稍后重试')
                self.move(c,who['subject'],None,n+fee+y.GAS,'withdraw:'+oid)
                paid=y.transfer(c,bank,b['address'],n,'yichat_wallet_withdraw',oid,'YiChat 钱包提现')
                if y.units(paid['credit'])!=n:raise RuntimeError('Withdrawal credit mismatch')
                tx=paid['hash']
            c.execute('INSERT INTO chat_wallet_operations VALUES(?,?,?,?,?,?,?,?,?,?)',(oid,who['subject'],key,kind,str(n),b['address'],b['created'],intent,tx,time.time()))
            return self.operation(c.execute('SELECT * FROM chat_wallet_operations WHERE id=?',(oid,)).fetchone())
    def operation(self,row):
        return {'id':row['id'],'kind':row['kind'],'amount':self.y.fmt(row['amount']),'fee':'0.5','gas':self.y.fmt(self.y.GAS),'state':'completed' if row['tx'] else 'awaiting_authorization','tx':row['tx'],'authorize_url':None if row['tx'] or not row['intent'] else self.y.ORIGIN+'/authorize/'+row['intent']}
    def deposit(self,who,oid):
        self.require();y=self.y
        with transaction(self.s.ledger) as c:
            self.auth(c,who);op=c.execute("SELECT * FROM chat_wallet_operations WHERE id=? AND uid=? AND kind='deposit'",(oid,who['subject'])).fetchone()
            if not op:fail(404,'充值不存在')
            if op['tx']:return self.operation(op)
            b=self.binding(c,who['username']);intent=c.execute('SELECT * FROM yidt_intents WHERE id=?',(op['intent'],)).fetchone();gross=int(op['amount'])+y.SCALE//2
            if not intent or intent['state']!='approved' or intent['expires']<=time.time() or intent['sid']!=who['sid'] or b['created']!=op['binding_stamp'] or b['address']!=op['address'] or int(intent['amount'])!=gross or intent['payload_hash']!=y.payload_hash('/api/wallet/deposits/'+oid+'/fund',{}):fail(409,'请先在 YIDT 授权此次充值；绑定或会话改变后请重新创建')
            if int(y.wallet(c,b['address'])['balance'])<gross+y.GAS:fail(409,'YIDT 钱包余额不足')
            tx=y.transfer(c,b['address'],self.bank(c),gross,'yichat_wallet_deposit',oid,'YiChat 钱包充值')
            if y.units(tx['credit'])!=gross:raise RuntimeError('Deposit credit mismatch')
            self.move(c,None,who['subject'],int(op['amount']),'deposit:'+oid)
            c.execute("UPDATE yidt_intents SET state='spent',tx=? WHERE id=?",(tx['hash'],op['intent']));c.execute('UPDATE chat_wallet_operations SET tx=? WHERE id=?',(tx['hash'],oid))
            return self.operation(c.execute('SELECT * FROM chat_wallet_operations WHERE id=?',(oid,)).fetchone())
