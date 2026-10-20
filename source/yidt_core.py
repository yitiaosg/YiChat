"""YIDT: integer atomic units, a fixed supply and transactional hash-chained ledger."""
from __future__ import annotations
import os, sqlite3, json, time, secrets, hashlib, re
from pathlib import Path
from decimal import Decimal, localcontext, ROUND_DOWN
from datetime import datetime, timezone, timedelta
from cryptography.fernet import Fernet
from mnemonic import Mnemonic
from werkzeug.security import generate_password_hash, check_password_hash

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('YIDT_DB', BASE / 'userdata.db'))
ACCOUNT_DB = Path(os.environ.get('YIDT_ACCOUNT_DB', BASE / 'account.db'))
KEY_PATH = Path(os.environ.get('YIDT_KEY', BASE / '.yidt_key'))
ORIGIN = 'https://yidt.yitiao.org'
OFFICIAL = 'yidt-000000000000'
SCALE = 10**16
CENT = 10**14
GAS = 39062500000000
SUPPLY = 2**32 * SCALE
SERVICES = {'ycards':'YCards', 'parachute':'跳伞', 'daily':'每日签到', 'wallet':'钱包转账', 'yichat':'YiChat'}
class Error(ValueError): pass
class PaymentRequired(Error):
    def __init__(self, service, amount, reason):
        self.service,self.amount,self.reason=service,amount,reason
        super().__init__('请前往 YIDT 确认付款')

def connect():
    c=sqlite3.connect(DB_PATH,timeout=30);c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000');c.execute('PRAGMA foreign_keys=ON')
    return c

def units(value):
    s=str(value)
    if len(s)>100 or not re.fullmatch(r'\d+(?:\.\d+)?',s):raise Error('请输入有效的非负金额')
    with localcontext() as ctx:
        ctx.prec=100
        n=int((Decimal(s)*SCALE).to_integral_value(rounding=ROUND_DOWN))
    if n>SUPPLY:raise Error('金额超过 YIDT 总量')
    return n

def fmt(n):
    n=int(n);sign='-' if n<0 else '';a,b=divmod(abs(n),SCALE)
    return sign+str(a)+(('.'+str(b).zfill(16).rstrip('0')) if b else '')

def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def utcday():return datetime.now(timezone.utc).date().isoformat()
def stamp():return datetime.now(timezone.utc).isoformat(timespec='microseconds')

def init(c):
    # No executescript here: it commits a caller's in-flight transaction.
    statements=[
    '''CREATE TABLE IF NOT EXISTS yidt_transfer_requests(token TEXT PRIMARY KEY,user TEXT,sid TEXT,address TEXT,payload_hash TEXT,tx TEXT)''',
    'CREATE TABLE IF NOT EXISTS yidt_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS yidt_wallets(address TEXT PRIMARY KEY,balance TEXT NOT NULL,
       secret TEXT,key_hash TEXT UNIQUE,mnemonic_hash TEXT UNIQUE,locked INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS yidt_members(user TEXT,address TEXT REFERENCES yidt_wallets(address),name TEXT NOT NULL,
       created TEXT NOT NULL,PRIMARY KEY(user,address))''',
    '''CREATE TABLE IF NOT EXISTS yidt_bindings(user TEXT,service TEXT,address TEXT REFERENCES yidt_wallets(address),
       locked INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL,PRIMARY KEY(user,service))''',
    '''CREATE TABLE IF NOT EXISTS yidt_transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,hash TEXT UNIQUE NOT NULL,
       previous TEXT NOT NULL,created TEXT NOT NULL,source TEXT,destination TEXT,amount TEXT NOT NULL,
       debit TEXT NOT NULL,credit TEXT NOT NULL,gas TEXT NOT NULL,kind TEXT NOT NULL,reference TEXT,note TEXT NOT NULL DEFAULT '')''',
    '''CREATE TABLE IF NOT EXISTS yidt_postings(tx INTEGER REFERENCES yidt_transactions(id),address TEXT,
       delta TEXT NOT NULL,PRIMARY KEY(tx,address))''',
    'CREATE INDEX IF NOT EXISTS yidt_postings_address ON yidt_postings(address,tx DESC)',
    '''CREATE TABLE IF NOT EXISTS yidt_intents(id TEXT PRIMARY KEY,user TEXT,sid TEXT,service TEXT,payload_hash TEXT,
       amount TEXT,reason TEXT,return_url TEXT,address TEXT,state TEXT NOT NULL,created REAL,expires REAL,tx TEXT,binding_stamp TEXT)''',
    '''CREATE TABLE IF NOT EXISTS yidt_pins(user TEXT PRIMARY KEY,hash TEXT NOT NULL,failures INTEGER NOT NULL DEFAULT 0,blocked_until REAL NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS yidt_sessions(sid TEXT PRIMARY KEY,user TEXT NOT NULL,expires REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS yidt_daily(user TEXT PRIMARY KEY,last_day TEXT,streak INTEGER NOT NULL DEFAULT 0,tx TEXT)''',
    '''CREATE TABLE IF NOT EXISTS yidt_escrow(service TEXT,reference TEXT,user TEXT,address TEXT,units TEXT NOT NULL,
       PRIMARY KEY(service,reference,user))''',
    '''CREATE TABLE IF NOT EXISTS yidt_routes(service TEXT,reference TEXT,user TEXT,address TEXT,PRIMARY KEY(service,reference,user))''',
    '''CREATE TABLE IF NOT EXISTS yidt_account_flows(token TEXT PRIMARY KEY,user TEXT NOT NULL,sid TEXT NOT NULL,expires REAL NOT NULL,method TEXT,return_to TEXT NOT NULL,state TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS yidt_reauth(token TEXT PRIMARY KEY,user TEXT,sid TEXT,expires REAL,used INTEGER NOT NULL DEFAULT 0)''',
    '''CREATE TABLE IF NOT EXISTS yidt_backup_challenges(token TEXT PRIMARY KEY,user TEXT,address TEXT,answer TEXT,expires REAL,attempts INTEGER NOT NULL DEFAULT 0)'''
    ]
    for s in statements:c.execute(s)

def cipher():
    if not KEY_PATH.exists():
        KEY_PATH.parent.mkdir(parents=True,exist_ok=True)
        try:
            fd=os.open(KEY_PATH,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
        except FileExistsError:pass
    return Fernet(KEY_PATH.read_bytes().strip())

def role(user):
    with sqlite3.connect(ACCOUNT_DB) as c:
        r=c.execute('SELECT role FROM users WHERE username=?',(user,)).fetchone()
    if not r:raise Error('账户不存在')
    return r[0]

def wallet(c,address):
    r=c.execute('SELECT * FROM yidt_wallets WHERE address=?',(address,)).fetchone()
    if not r:raise Error('钱包地址不存在')
    return r

def member(c,user,address):
    if not c.execute('SELECT 1 FROM yidt_members WHERE user=? AND address=?',(user,address)).fetchone():raise Error('请先导入此钱包')
    return wallet(c,address)

def create_wallet(c,user,import_secret=None):
    limit=1 if role(user)=='guest' else 16
    if c.execute('SELECT COUNT(*) FROM yidt_members WHERE user=?',(user,)).fetchone()[0]>=limit:raise Error(f'当前账户最多持有 {limit} 个钱包')
    mn=Mnemonic('english'); words=None
    if import_secret:
        raw=' '.join(import_secret.strip().lower().split())
        if len(raw.split())==12:
            if not mn.check(raw):raise Error('助记词校验失败')
            words=raw;private=hashlib.sha256(mn.to_seed(words)+b'YIDT wallet v1').hexdigest()
        elif re.fullmatch('[0-9a-f]{64}',raw):private=raw
        else:raise Error('请输入 12 个英文助记词或 64 位十六进制私钥')
    else:
        words=mn.generate(128);private=hashlib.sha256(mn.to_seed(words)+b'YIDT wallet v1').hexdigest()
    kh=hashlib.sha256(bytes.fromhex(private)).hexdigest()
    old=c.execute('SELECT address FROM yidt_wallets WHERE key_hash=?',(kh,)).fetchone()
    if old:address=old['address']
    else:
        if import_secret and words is None:raise Error('此私钥未对应已存在的 YIDT 钱包，请使用原助记词创建或导入')
        address='yidt-'+''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(16))
        encrypted=cipher().encrypt(canonical({'mnemonic':words,'private_key':private}).encode()).decode()
        c.execute('INSERT INTO yidt_wallets(address,balance,secret,key_hash,mnemonic_hash,created) VALUES(?,?,?,?,?,?)',
                  (address,'0',encrypted,kh,hashlib.sha256(words.encode()).hexdigest(),stamp()))
    if c.execute('SELECT 1 FROM yidt_members WHERE user=? AND address=?',(user,address)).fetchone():raise Error('当前账户已导入该钱包')
    names={r[0] for r in c.execute('SELECT name FROM yidt_members WHERE user=?',(user,))};i=1
    while f'wallet {i}' in names:i+=1
    c.execute('INSERT INTO yidt_members VALUES(?,?,?,?)',(user,address,f'wallet {i}',stamp()))
    return address

def _record(c,source,destination,amount,debit,credit,gas,kind,reference,note,changes):
    previous=c.execute('SELECT hash FROM yidt_transactions ORDER BY id DESC LIMIT 1').fetchone()
    data=dict(previous=previous[0] if previous else '0'*64,created=stamp(),source=source,destination=destination,
              amount=str(amount),debit=str(debit),credit=str(credit),gas=str(gas),kind=kind,reference=reference,note=note)
    digest=hashlib.sha256(canonical(data).encode()).hexdigest()
    tx=c.execute('INSERT INTO yidt_transactions(hash,previous,created,source,destination,amount,debit,credit,gas,kind,reference,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
      (digest,*[data[k] for k in ('previous','created','source','destination','amount','debit','credit','gas','kind','reference','note')])).lastrowid
    for address,delta in changes.items():
        before=int(wallet(c,address)['balance']);after=before+delta
        if after<0:raise Error('钱包余额不足')
        c.execute('UPDATE yidt_wallets SET balance=? WHERE address=?',(str(after),address))
        c.execute('INSERT INTO yidt_postings VALUES(?,?,?)',(tx,address,str(delta)))
    return dict(hash=digest,amount=fmt(amount),debit=fmt(debit),credit=fmt(credit),gas=fmt(gas))

def quote(c,source,amount,official=False):
    if amount<=0:raise Error('转账金额必须大于零')
    available=int(wallet(c,source)['balance'])
    if source==OFFICIAL:
        available-=sum(int(r[0]) for r in c.execute('SELECT units FROM yidt_escrow'))
    if official or source==OFFICIAL:debit=amount;credit=amount-GAS
    elif available>=amount+GAS:debit=amount+GAS;credit=amount
    elif available>=amount:debit=available;credit=available-GAS
    else:raise Error('钱包余额不足')
    if available<debit or credit<=0:raise Error('余额或转账金额不足以支付 gas')
    return debit,credit

def transfer(c,source,destination,amount,kind='transfer',reference=None,note=''):
    wallet(c,destination)
    if source==destination:raise Error('不能向相同钱包转账')
    debit,credit=quote(c,source,amount)
    changes={}
    for a,n in ((source,-debit),(destination,credit),(OFFICIAL,GAS)):changes[a]=changes.get(a,0)+n
    if sum(changes.values())!=0:raise RuntimeError('Unbalanced transfer')
    return _record(c,source,destination,amount,debit,credit,GAS,kind,reference,note[:500],changes)

def binding(c,user,service):return c.execute('SELECT * FROM yidt_bindings WHERE user=? AND service=?',(user,service)).fetchone()
def bound_address(c,user,service):
    b=binding(c,user,service)
    if not b:raise Error('绑定 YIDT 钱包以继续')
    return b['address']

def sid_valid(c,sid,user=None):
    r=c.execute('SELECT * FROM yidt_sessions WHERE sid=? AND revoked=0 AND expires>?',(sid,time.time())).fetchone()
    return bool(r and (user is None or r['user']==user))

def payload_hash(path,data):return hashlib.sha256(canonical([path,data]).encode()).hexdigest()

def service_debit(c,user,service,amount,reason,reference):
    from flask import has_request_context,request,session,g
    if not has_request_context():raise Error('付款必须经用户在 YIDT 确认')
    token=request.headers.get('X-YIDT-Intent','')
    row=c.execute('SELECT * FROM yidt_intents WHERE id=?',(token,)).fetchone()
    digest=payload_hash(request.path,request.get_json(silent=True) or {})
    if not row:raise PaymentRequired(service,amount,reason)
    if (row['user']!=user or row['sid']!=session.get('yidt_sid') or row['service']!=service or
        row['payload_hash']!=digest or int(row['amount'])!=amount or row['state']!='approved' or row['expires']<time.time() or
        not sid_valid(c,row['sid'],user)):
        raise Error('支付授权失效或订单已变化，请重新确认')
    b=binding(c,user,service)
    if not b or b['address']!=row['address'] or b['created']!=row['binding_stamp']:raise Error('钱包绑定已变化，请重新授权')
    result=transfer(c,b['address'],OFFICIAL,amount,'payment',reference,reason)
    c.execute("UPDATE yidt_intents SET state='spent',tx=? WHERE id=?",(result['hash'],token))
    c.execute('INSERT INTO yidt_routes VALUES(?,?,?,?) ON CONFLICT(service,reference,user) DO UPDATE SET address=excluded.address',(service,str(reference),user,b['address']))
    g.yidt_tx=result['hash']
    return result

def service_credit(c,user,service,amount,reason,reference,address=None):
    if amount<=GAS:return None
    route=c.execute('SELECT address FROM yidt_routes WHERE service=? AND reference=? AND user=?',(service,str(reference),user)).fetchone()
    target=address or (route['address'] if route else bound_address(c,user,service))
    return transfer(c,OFFICIAL,target,amount,'payout',str(reference),reason)

def pin_ok(c,user,pin):
    r=c.execute('SELECT * FROM yidt_pins WHERE user=?',(user,)).fetchone()
    if not r:raise Error('请先设置六位支付密码')
    if r['blocked_until']>time.time():raise Error('尝试次数过多，请 15 分钟后再试')
    if not re.fullmatch('[0-9]{6}',pin or '') or not check_password_hash(r['hash'],pin):
        n=r['failures']+1
        c.execute('UPDATE yidt_pins SET failures=?,blocked_until=? WHERE user=?',(n,time.time()+900 if n>=5 else 0,user))
        # Caller must commit the failure counter, even when rejecting the action.
        return False
    c.execute('UPDATE yidt_pins SET failures=0,blocked_until=0 WHERE user=?',(user,));return True

def daily(c,user):
    a=bound_address(c,user,'daily');r=c.execute('SELECT * FROM yidt_daily WHERE user=?',(user,)).fetchone();day=utcday()
    if r and r['last_day']==day:raise Error('今天已经领取过了')
    yesterday=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat()
    streak=r['streak']+1 if r and r['last_day']==yesterday else 1
    reward=(300 if streak%5==0 else 100)*SCALE
    tx=transfer(c,OFFICIAL,a,reward,'daily',day,'每日签到')
    c.execute('INSERT INTO yidt_daily VALUES(?,?,?,?) ON CONFLICT(user) DO UPDATE SET last_day=excluded.last_day,streak=excluded.streak,tx=excluded.tx',(user,day,streak,tx['hash']))
    return tx

def wallet_assets(c,address):
    bal=int(wallet(c,address)['balance'])
    escrow=sum(int(r[0]) for r in c.execute('SELECT units FROM yidt_escrow WHERE address=?',(address,)))
    return bal+escrow,escrow

def dashboard(c,user):
    items=[];total=change=0
    for r in c.execute('SELECT m.*,w.balance,w.locked FROM yidt_members m JOIN yidt_wallets w USING(address) WHERE m.user=? ORDER BY m.created',(user,)):
        asset,escrow=wallet_assets(c,r['address'])
        delta=sum(int(p[0]) for p in c.execute('SELECT p.delta FROM yidt_postings p JOIN yidt_transactions t ON t.id=p.tx WHERE p.address=? AND t.created>=?',(r['address'],utcday()+'T00:00:00')))
        # Escrow movement annotations offset spendable movement until final settlement.
        adj=c.execute('SELECT value FROM yidt_meta WHERE key=?',('delta:'+utcday()+':'+r['address'],)).fetchone()
        delta+=int(adj[0]) if adj else 0
        total+=asset;change+=delta
        items.append(dict(r)|dict(assets=fmt(asset),escrow=fmt(escrow),balance=fmt(r['balance']),change=fmt(delta)))
    with localcontext() as ctx:
        ctx.prec=80
        percent=fmt(int(Decimal(change)*100*SCALE/(total-change)))+'%' if total!=change else '—'
    addresses=[x['address'] for x in items]
    history=[]
    if addresses:
        marks=','.join('?' for _ in addresses)
        history=[dict(r) for r in c.execute(f'SELECT DISTINCT t.* FROM yidt_transactions t JOIN yidt_postings p ON p.tx=t.id WHERE p.address IN ({marks}) ORDER BY t.id DESC LIMIT 5',addresses)]
    return dict(wallets=items,total=fmt(total),change=fmt(change),percent=percent,history=history)

def escrow_set(c,service,reference,user,address,value):
    old=c.execute('SELECT * FROM yidt_escrow WHERE service=? AND reference=? AND user=?',(service,reference,user)).fetchone()
    # An escrow remains attributed to the original payment wallet.
    if old and int(old['units']) and old['address']!=address:raise Error('此拍卖仍有托管款，请使用原钱包继续出价')
    before=int(old['units']) if old else 0
    key='delta:'+utcday()+':'+address
    row=c.execute('SELECT value FROM yidt_meta WHERE key=?',(key,)).fetchone()
    c.execute('INSERT INTO yidt_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str((int(row[0]) if row else 0)+value-before)))
    c.execute('INSERT INTO yidt_escrow VALUES(?,?,?,?,?) ON CONFLICT(service,reference,user) DO UPDATE SET address=excluded.address,units=excluded.units',(service,reference,user,address,str(value)))

def verify_chain(c):
    previous='0'*64;balances={}
    for r in c.execute('SELECT * FROM yidt_transactions ORDER BY id'):
        d={k:r[k] for k in ('previous','created','source','destination','amount','debit','credit','gas','kind','reference','note')}
        if r['previous']!=previous or hashlib.sha256(canonical(d).encode()).hexdigest()!=r['hash']:raise Error('账本哈希校验失败')
        entries={p['address']:int(p['delta']) for p in c.execute('SELECT * FROM yidt_postings WHERE tx=?',(r['id'],))}
        expected={}
        if r['kind']=='genesis':expected={OFFICIAL:SUPPLY}
        else:
            for a,n in ((r['source'],-int(r['debit'])),(r['destination'],int(r['credit'])),(OFFICIAL,int(r['gas']))):expected[a]=expected.get(a,0)+n
        if entries!=expected or sum(entries.values())!=(SUPPLY if r['kind']=='genesis' else 0):raise Error('账本不平衡')
        for a,n in entries.items():balances[a]=balances.get(a,0)+n
        previous=r['hash']
    for r in c.execute('SELECT address,balance FROM yidt_wallets'):
        if balances.get(r['address'],0)!=int(r['balance']) or int(r['balance'])<0:raise Error('钱包余额校验失败')
    if sum(balances.values())!=SUPPLY:raise Error('总量不符')
    return previous

def migrate():
    with connect() as c:
        c.execute('BEGIN IMMEDIATE');init(c)
        if c.execute("SELECT 1 FROM yidt_meta WHERE key='migrated'").fetchone():return verify_chain(c)
        c.execute('INSERT INTO yidt_wallets(address,balance,created) VALUES(?,?,?)',(OFFICIAL,'0',stamp()))
        _record(c,None,OFFICIAL,SUPPLY,0,SUPPLY,0,'genesis',None,'YIDT fixed supply',{OFFICIAL:SUPPLY})
        with sqlite3.connect(ACCOUNT_DB) as ac:users=[r[0] for r in ac.execute('SELECT username FROM users')]
        for user in users:
            a=create_wallet(c,user)
            old=c.execute('SELECT * FROM points_accounts WHERE user=?',(user,)).fetchone()
            n=int(old['balance_cents'])*CENT if old else 0
            if n:_record(c,OFFICIAL,a,n,n,n,0,'migration',None,'', {OFFICIAL:-n,a:n})
            if old and old['last_checkin']:
                c.execute('INSERT INTO yidt_daily(user,last_day,streak) VALUES(?,?,?)',(user,old['last_checkin'][:10],old['streak']))
        c.execute("INSERT INTO yidt_meta VALUES('migrated',?)",(stamp(),))
        return verify_chain(c)

if __name__=='__main__':print(migrate())
