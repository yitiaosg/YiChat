"""YIDT wallet and public explorer, served on yidt.yitiao.org."""
import base64,hashlib,io,json,secrets,time,os
from urllib.parse import urlencode
from flask import Flask,request,session,redirect,render_template,abort,Response
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash
from joserfc import jwt
from joserfc.jwk import KeySet
import qrcode,requests
import yidt_core as y
import passkeys as pk

app=Flask(__name__)
y.cipher()
app.secret_key=hashlib.sha256(y.KEY_PATH.read_bytes()+b'yidt cookie v1').digest()
app.config.update(SESSION_COOKIE_NAME='yidt_session',SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',MAX_CONTENT_LENGTH=16384)
app.wsgi_app=ProxyFix(app.wsgi_app,x_proto=1,x_host=1)
app.jinja_env.globals.update(fmt=y.fmt,official=y.OFFICIAL,services=y.SERVICES,origin=y.ORIGIN)
wallet_passkeys=pk.Store(y.connect,'yidt.yitiao.org','YIDT',[y.ORIGIN])
with wallet_passkeys.db():pass

@app.before_request
def guard():
    if request.path.startswith(('/explorer','/static/','/health','/login','/oauth/callback')):return
    with y.connect() as c:
        if not y.sid_valid(c,session.get('sid'),session.get('user')):
            session.clear();session['next']=request.full_path.rstrip('?')
            return render_template('yidt.html',screen='login',title='使用 Yitiao 账户登录')
    session.setdefault('csrf',secrets.token_urlsafe(32))
    if request.method=='POST' and not secrets.compare_digest(request.headers.get('X-Passkey-CSRF',request.form.get('csrf','')),session['csrf']):abort(403)

@app.context_processor
def account_header():
    # Same shared avatar file and username as the main homepage.
    from pathlib import Path
    from flask import url_for
    user=session.get('user')
    avatar=None
    if user and not request.path.startswith('/explorer'):
        path=Path(app.static_folder)/'avatar'/f'{user}.jpg'
        if path.is_file():avatar=url_for('static',filename=f'avatar/{user}.jpg',v=int(path.stat().st_mtime))
    return {'account_avatar':avatar,'wallet_has_passkeys':bool(user and not request.path.startswith('/explorer') and wallet_passkeys.has(user))}

@app.after_request
def secure(r):
    r.headers['Cache-Control']='no-store';r.headers['Referrer-Policy']='no-referrer'
    r.headers['X-Content-Type-Options']='nosniff';r.headers['X-Frame-Options']='DENY'
    r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self' https://yitiao.org https://chat.yitiao.org; base-uri 'none'"
    return r

@app.errorhandler(y.Error)
def error(e):return render_template('yidt.html',screen='error',title='操作未完成',error=str(e),public=request.path.startswith('/explorer')),409

@app.get('/session-state')
def session_state():return {'ok':True}

@app.get('/health')
def health():return {'ok':True,'service':'yidt'}

@app.get('/login')
def login():
    if request.args.get('next')=='/explorer/':session['next']='/explorer/'
    state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48);nonce=secrets.token_urlsafe(32)
    session.update(oauth_state=state,verifier=verifier,nonce=nonce)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    return redirect('https://yitiao.org/oidc/authorize?'+urlencode(dict(client_id='yidt',response_type='code',scope='openid profile',redirect_uri=y.ORIGIN+'/oauth/callback',state=state,nonce=nonce,code_challenge=challenge,code_challenge_method='S256')))

@app.get('/oauth/callback')
def callback():
    state=session.pop('oauth_state',None)
    if not state or not secrets.compare_digest(request.args.get('state',''),state):raise y.Error('登录状态校验失败，请重试')
    try:
        r=requests.post(os.environ.get('YIDT_IDP_INTERNAL','http://127.0.0.1:5001')+'/oidc/token',data=dict(client_id='yidt',grant_type='authorization_code',code=request.args.get('code',''),redirect_uri=y.ORIGIN+'/oauth/callback',code_verifier=session.pop('verifier','')),timeout=15)
        r.raise_for_status()
        keys=requests.get(os.environ.get('YIDT_IDP_INTERNAL','http://127.0.0.1:5001')+'/oidc/jwks',timeout=10);keys.raise_for_status()
        token=jwt.decode(r.json()['id_token'],KeySet.import_key_set(keys.json()),algorithms=['RS256']).claims
        if token.get('iss')!='https://yitiao.org' or token.get('aud')!='yidt' or token.get('exp',0)<=time.time() or token.get('nonce')!=session.pop('nonce',None):raise ValueError()
        username=token.get('preferred_username') or token['sub']
        with y.connect() as c:
            if not y.sid_valid(c,token.get('sid'),username):raise ValueError()
    except Exception:raise y.Error('Yitiao 登录验证失败，请返回重试')
    target=session.get('next','/');session.clear();session.update(user=username,sid=token['sid'],csrf=secrets.token_urlsafe(32))
    return redirect(target if target.startswith('/') and not target.startswith('//') and chr(92) not in target and chr(13) not in target and chr(10) not in target else '/')

def pin_check(c):
    if request.form.get('passkey_response'):
        try:
            proof=json.loads(request.form['passkey_response'])
            if not isinstance(proof,dict):raise ValueError()
            wallet_passkeys.authenticate(c,session['user'],session['sid'],'payment',pk.form_context(request.path,request.form),proof)
            return
        except (pk.Error,ValueError,TypeError) as exc:
            c.commit();raise y.Error(str(exc) if isinstance(exc,pk.Error) else '通行密钥验证失败，请重试')
    if not y.pin_ok(c,session['user'],request.form.get('pin','')):
        c.commit();raise y.Error('支付密码错误')

def need_pin(c):return bool(c.execute('SELECT 1 FROM yidt_pins WHERE user=?',(session['user'],)).fetchone())

@app.route('/',methods=['GET','POST'])
def home():
    user=session['user']
    with y.connect() as c:
        if request.method=='POST':
            c.execute('BEGIN IMMEDIATE');a=y.create_wallet(c,user,request.form.get('secret') or None)
            return redirect('/wallet/'+a)
        data=y.dashboard(c,user)
    return render_template('yidt.html',screen='home',title='YIDT',data=data)

@app.route('/pin/',methods=['GET','POST'])
def pin():
    token=request.args.get('flow','');user=session['user'];failure=''
    with y.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        flow=c.execute("SELECT * FROM yidt_account_flows WHERE token=? AND user=? AND sid=? AND state='verified' AND expires>?",(token,user,session['sid'],time.time())).fetchone()
        if request.method=='POST':
            if not flow:raise y.Error('验证已过期或已使用，请重新验证账户')
            value=request.form.get('new_pin','')
            if not y.re.fullmatch('[0-9]{6}',value) or value!=request.form.get('confirm_pin'):
                failure='请输入两次相同的六位数字支付密码'
            else:
                c.execute('INSERT INTO yidt_pins(user,hash) VALUES(?,?) ON CONFLICT(user) DO UPDATE SET hash=excluded.hash,failures=0,blocked_until=0',(user,generate_password_hash(value)))
                c.execute("UPDATE yidt_account_flows SET state='used' WHERE user=? AND state='verified'",(user,))
                c.execute("UPDATE yidt_intents SET state='cancelled' WHERE user=? AND state IN ('pending','approved')",(user,))
                wallet_passkeys.init(c)
                c.execute('DELETE FROM passkey_challenges WHERE user=?',(user,))
                return redirect('/pin/complete')
        configured=need_pin(c)
    return render_template('yidt.html',screen='pin',title='支付密码管理',valid=bool(flow),configured=configured,failure=failure,expired=bool(token and not flow))

@app.get('/pin/complete')
def pin_complete():
    target=session.pop('pin_return','/')
    return redirect(target if target.startswith('/') and not target.startswith('//') and chr(92) not in target else '/')

@app.route('/services/<service>/',methods=['GET','POST'])
def services(service):
    if service not in y.SERVICES or service=='wallet':abort(404)
    user=session['user']
    with y.connect() as c:
        c.execute('BEGIN IMMEDIATE');b=y.binding(c,user,service)
        if request.method=='POST':
            action=request.form.get('action','bind')
            if action=='unbind':
                if b and b['locked']:pin_check(c)
                c.execute('DELETE FROM yidt_bindings WHERE user=? AND service=?',(user,service));return redirect(request.path)
            if not need_pin(c):session['pin_return']=request.path;return redirect('/pin/')
            address=request.form.get('address','');y.member(c,user,address)
            if b and b['locked']:pin_check(c)
            c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,?,?) ON CONFLICT(user,service) DO UPDATE SET address=excluded.address,locked=excluded.locked,created=excluded.created',
              (user,service,address,int(request.form.get('locked')=='on'),y.stamp()))
            from yidt_bridge import RETURN
            return redirect(RETURN[service])
        wallets=list(c.execute('SELECT * FROM yidt_members WHERE user=? ORDER BY created',(user,)))
        balance=y.fmt(y.wallet(c,b['address'])['balance']) if b else None
    return render_template('yidt.html',screen='service',title=y.SERVICES[service]+' · 钱包授权',service=service,binding=b,wallets=wallets,balance=balance)

@app.route('/authorize/<token>',methods=['GET','POST'])
def authorize(token):
    with y.connect() as c:
        c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM yidt_intents WHERE id=?',(token,)).fetchone()
        if not r or r['user']!=session['user'] or r['sid']!=session['sid'] or r['expires']<time.time() or r['state'] not in ('pending','approved'):raise y.Error('支付请求已过期，请返回服务重新操作')
        b=y.binding(c,r['user'],r['service'])
        if not b or b['address']!=r['address'] or b['created']!=r['binding_stamp']:raise y.Error('钱包绑定已变化，请重新发起付款')
        debit,credit=y.quote(c,r['address'],int(r['amount']))
        if request.method=='POST':
            if request.form.get('action')=='cancel':
                c.execute("UPDATE yidt_intents SET state='cancelled' WHERE id=?",(token,));return redirect(r['return_url'])
            if b['locked']:pin_check(c)
            c.execute("UPDATE yidt_intents SET state='approved' WHERE id=?",(token,))
            # Native return carries only the fixed order reference. The App
            # queries its original order and requires another explicit fund.
            if r['return_url'].startswith('https://chat.yitiao.org/app/return?order='):
                return redirect(r['return_url'])
            return redirect(r['return_url']+('&' if '?' in r['return_url'] else '?')+urlencode({'yidt_intent':token}))
    return render_template('yidt.html',screen='authorize',title='确认付款',intent=r,binding=b,debit=y.fmt(debit),credit=y.fmt(credit))

@app.route('/wallet/<address>',methods=['GET','POST'])
def wallet(address):
    user=session['user']
    with y.connect() as c:
        c.execute('BEGIN IMMEDIATE');w=y.member(c,user,address)
        if request.method=='POST':
            action=request.form.get('action')
            if action=='rename':
                name=request.form.get('name','').strip()
                if not 1<=len(name)<=20:raise y.Error('钱包名称须为 1～20 个字符')
                c.execute('UPDATE yidt_members SET name=? WHERE user=? AND address=?',(name,user,address))
            elif action=='remove':
                if w['locked']:pin_check(c)
                c.execute('DELETE FROM yidt_members WHERE user=? AND address=?',(user,address));return redirect('/')
            elif action=='lock':
                if not need_pin(c):session['pin_return']=request.path;return redirect('/pin/')
                pin_check(c);c.execute('UPDATE yidt_wallets SET locked=? WHERE address=?',(int(not w['locked']),address))
            elif action in ('unbind','service_lock'):
                b=c.execute('SELECT * FROM yidt_bindings WHERE user=? AND service=? AND address=?',(request.form.get('owner'),request.form.get('service'),address)).fetchone()
                if not b:raise y.Error('绑定已变化')
                # Shared wallet controllers can revoke any grant; changing a grant's lock requires its owner.
                if action=='service_lock' and b['user']!=user:raise y.Error('只能更改自己的服务支付密码设置')
                if w['locked'] or (b['locked'] and b['user']==user):pin_check(c)
                if action=='unbind':c.execute('DELETE FROM yidt_bindings WHERE user=? AND service=?',(b['user'],b['service']))
                else:
                    if not need_pin(c):session['pin_return']=request.path;return redirect('/pin/')
                    c.execute('UPDATE yidt_bindings SET locked=?,created=? WHERE user=? AND service=?',(int(not b['locked']),y.stamp(),user,b['service']))
            return redirect(request.path)
        name=c.execute('SELECT name FROM yidt_members WHERE user=? AND address=?',(user,address)).fetchone()[0]
        bindings=list(c.execute('SELECT * FROM yidt_bindings WHERE address=?',(address,)))
        history=list(c.execute('SELECT t.* FROM yidt_transactions t JOIN yidt_postings p ON p.tx=t.id WHERE p.address=? ORDER BY t.id DESC LIMIT 50',(address,)))
    return render_template('yidt.html',screen='wallet',title=name,wallet=w,bindings=bindings,history=history)

@app.route('/wallet/<address>/send',methods=['GET','POST'])
def send(address):
    with y.connect() as c:
        c.execute('BEGIN IMMEDIATE');w=y.member(c,session['user'],address)
        if request.method=='POST':
            if w['locked']:pin_check(c)
            token=request.form.get('token','')
            digest=y.payload_hash(address,[request.form.get('amount'),request.form.get('destination'),request.form.get('note')])
            old=c.execute('SELECT * FROM yidt_transfer_requests WHERE token=?',(token,)).fetchone()
            if old:
                if old['user']!=session['user'] or old['sid']!=session['sid'] or old['payload_hash']!=digest:raise y.Error('转账请求不匹配')
                return redirect('/explorer/tx/'+old['tx'])
            if not token or token!=session.get('transfer_token'):raise y.Error('转账请求已失效，请刷新')
            amount=y.units(request.form.get('amount',''));target=request.form.get('destination','').strip().lower()
            tx=y.transfer(c,address,target,amount,note=request.form.get('note',''))
            c.execute('INSERT INTO yidt_transfer_requests VALUES(?,?,?,?,?,?)',(token,session['user'],session['sid'],address,digest,tx['hash']))
            session.pop('transfer_token',None);return redirect('/explorer/tx/'+tx['hash'])
        session['transfer_token']=secrets.token_urlsafe(32)
    return render_template('yidt.html',screen='send',title='转出 YIDT',wallet=w)

@app.get('/wallet/<address>/receive')
def receive(address):
    with y.connect() as c:w=y.member(c,session['user'],address)
    return render_template('yidt.html',screen='receive',title='转入 YIDT',wallet=w)

@app.get('/wallet/<address>/qr')
def qr(address):
    with y.connect() as c:y.member(c,session['user'],address)
    b=io.BytesIO();qrcode.make(address).save(b,format='PNG');return Response(b.getvalue(),mimetype='image/png')

@app.route('/wallet/<address>/backup',methods=['GET','POST'])
def backup(address):
    user=session['user'];content=None;challenge=None;question=None;done=False
    with y.connect() as c:
        c.execute('BEGIN IMMEDIATE');w=y.member(c,user,address)
        if request.method=='POST':
            if request.form.get('action')=='answer':
                r=c.execute('SELECT * FROM yidt_backup_challenges WHERE token=? AND user=? AND address=?',(request.form.get('challenge',''),user,address)).fetchone()
                if not r or r['expires']<time.time() or r['attempts']>=5:raise y.Error('检查已过期，请重新记录')
                answer=hashlib.sha256(request.form.get('answer','').strip().lower().encode()).hexdigest()
                c.execute('UPDATE yidt_backup_challenges SET attempts=attempts+1 WHERE token=?',(r['token'],))
                if not secrets.compare_digest(r['answer'],answer):c.commit();raise y.Error('答案不正确，请重新记录')
                c.execute('DELETE FROM yidt_backup_challenges WHERE token=?',(r['token'],));done=True
            else:
                if not need_pin(c):session['pin_return']=request.path;return redirect('/pin/')
                pin_check(c)
                data=json.loads(y.cipher().decrypt(w['secret'].encode()));kind=request.form.get('kind')
                if kind=='mnemonic':
                    words=data['mnemonic'].split();index=secrets.randbelow(12);answer=words[index];question=f'请输入第 {index+1} 个助记词';content=data['mnemonic']
                else:answer=data['private_key'][-3:];question='请输入私钥最后 3 个字符';content=data['private_key']
                challenge=secrets.token_urlsafe(32)
                c.execute('INSERT INTO yidt_backup_challenges VALUES(?,?,?,?,?,0)',(challenge,user,address,hashlib.sha256(answer.encode()).hexdigest(),time.time()+300))
    return render_template('yidt.html',screen='backup',title='安全记录钱包',wallet=w,content=content,challenge=challenge,question=question,done=done)

@app.get('/explorer/')
def explorer():
    q=request.args.get('q','').strip()
    if q:
        if (q==y.OFFICIAL or y.re.fullmatch('yidt-[a-z0-9]{16}',q)):return redirect('/explorer/address/'+q)
        if y.re.fullmatch('[a-f0-9]{64}',q):return redirect('/explorer/tx/'+q)
        return render_template('yidt.html',screen='error',title='YIDT Explorer',public=True,error='请输入完整钱包地址或 64 位交易哈希，不支持模糊搜索'),400
    with y.connect() as c:
        signed_in=y.sid_valid(c,session.get('sid'),session.get('user'))
        history=list(c.execute('''SELECT DISTINCT t.* FROM yidt_transactions t
            JOIN yidt_postings p ON p.tx=t.id JOIN yidt_members m ON m.address=p.address
            WHERE m.user=? ORDER BY t.id DESC LIMIT 50''',(session['user'],))) if signed_in else []
    return render_template('yidt.html',screen='explorer',title='YIDT Explorer',public=True,history=history,own_history=signed_in)

@app.get('/explorer/tx/<digest>')
def transaction(digest):
    with y.connect() as c:
        tx=c.execute('SELECT * FROM yidt_transactions WHERE hash=?',(digest,)).fetchone()
        if not tx:abort(404)
        can_view_note=y.sid_valid(c,session.get('sid'),session.get('user')) and bool(c.execute('SELECT 1 FROM yidt_members WHERE user=? AND address IN (?,?)',(session['user'],tx['source'],tx['destination'])).fetchone())
    return render_template('yidt.html',screen='transaction',title='交易详情',public=True,tx=tx,can_view_note=can_view_note)

@app.get('/explorer/address/<address>')
def address_page(address):
    try:page=max(1,int(request.args.get('page','1')))
    except ValueError:page=1
    with y.connect() as c:
        w=y.wallet(c,address)
        history=list(c.execute('SELECT t.* FROM yidt_transactions t JOIN yidt_postings p ON p.tx=t.id WHERE p.address=? ORDER BY t.id DESC LIMIT 50 OFFSET ?',(address,(page-1)*50)))
    return render_template('yidt.html',screen='address',title='钱包账本',public=True,wallet=w,history=history,page=page)

@app.errorhandler(pk.Error)
def passkey_error(exc):return {'error':str(exc)},400


def wallet_passkey_json():
    if request.headers.get('Origin')!=y.ORIGIN or not request.is_json:abort(403)
    data=request.get_json()
    if not isinstance(data,dict):abort(400)
    return data


@app.get('/passkeys/')
def manage_passkeys():
    with y.connect() as c:ready=need_pin(c)
    return render_template('yidt.html',screen='passkeys',title='YIDT 通行密钥',passkeys=wallet_passkeys.items(session['user']),pk_api='/passkeys',pk_csrf=session['csrf'],pk_wallet=True,pk_ready=ready)


def wallet_passkey_management_pin(c,data):
    if not need_pin(c):raise pk.Error('请先设置支付密码，再设置通行密钥')
    if not y.pin_ok(c,session['user'],str(data.get('pin') or '')):
        c.commit();raise pk.Error('支付密码错误')


@app.post('/passkeys/register/options')
def wallet_passkey_registration_options():
    data=wallet_passkey_json()
    with y.connect() as c:
        c.execute('BEGIN IMMEDIATE');wallet_passkey_management_pin(c,data)
    return wallet_passkeys.registration_options(session['user'],session['sid'])


@app.post('/passkeys/register/finish')
def wallet_passkey_registration_finish():
    data=wallet_passkey_json()
    with wallet_passkeys.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if not need_pin(c):raise pk.Error('请先设置支付密码')
        try:wallet_passkeys.register(c,session['user'],session['sid'],data)
        except pk.Error:c.commit();raise
    return {'ok':True,'next':'/passkeys/'}


@app.post('/passkeys/manage')
def wallet_passkey_management():
    data=wallet_passkey_json()
    with wallet_passkeys.db() as c:
        c.execute('BEGIN IMMEDIATE');wallet_passkey_management_pin(c,data)
        wallet_passkeys.manage(c,session['user'],data)
    return {'ok':True,'next':'/passkeys/'}


@app.post('/passkeys/auth/options')
def wallet_passkey_authentication_options():
    from werkzeug.datastructures import MultiDict
    data=wallet_passkey_json();path=data.get('path','');fields=data.get('fields')
    if not isinstance(path,str) or not path.startswith(('/authorize/','/wallet/','/services/')) or not isinstance(fields,list) or len(fields)>30:abort(400)
    if any(not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(x,str) and len(x)<4000 for x in pair) for pair in fields):abort(400)
    context=pk.form_context(path,MultiDict(fields))
    return wallet_passkeys.authentication_options(session['user'],session['sid'],'payment',context)

if __name__=='__main__':app.run(host='127.0.0.1',port=5010)
