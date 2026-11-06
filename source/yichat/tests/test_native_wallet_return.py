"""Original YIDT binding handler in an isolated Flask app and fixture ledger."""
import secrets,sys,time
from pathlib import Path
import pytest
from backend.db import connect
from test_gateway import gateway

@pytest.fixture
def wallet(env):
    s,f,u=env
    # Match existing fixture dependency discovery without installing into production.
    optional=Path('/opt/vps-python/lib/python3.11/site-packages')
    if optional.exists() and str(optional) not in sys.path:sys.path.append(str(optional))
    import yidt
    from flask import Flask
    from werkzeug.security import generate_password_hash
    app=Flask('isolated_native_wallet');app.secret_key='isolated-native-wallet-secret'
    app.before_request(yidt.guard)
    app.add_url_rule('/services/<service>/',view_func=yidt.services,methods=['GET','POST'])
    app.add_url_rule('/authorize/<token>',view_func=yidt.authorize,methods=['GET','POST'])
    with connect(s.ledger) as c:
        address=c.execute("SELECT address FROM yidt_bindings WHERE user='alice' AND service='yichat'").fetchone()[0]
        c.execute('INSERT INTO yidt_pins(user,hash) VALUES(?,?)',('alice',generate_password_hash('123456')))
    client=app.test_client()
    with client.session_transaction(base_url='https://yidt.yitiao.org') as session:
        session.update(user='alice',sid='sid-alice',csrf='isolated-csrf')
    return client,s,address

@pytest.mark.parametrize('service,query,target',[
    ('yichat','?client=android','https://chat.yitiao.org/app/return'),
    ('yichat','','https://chat.yitiao.org/'),
    ('yichat','?client=https://evil.example/','https://chat.yitiao.org/'),
    ('yichat','?client=desktop','https://chat.yitiao.org/app/desktop-return'),
    ('yichat','?client=Desktop','https://chat.yitiao.org/'),
    ('yichat','?client=windows','https://chat.yitiao.org/'),
    ('yichat','?client=desktop%0a','https://chat.yitiao.org/'),
    ('yichat','?client=','https://chat.yitiao.org/'),
])
def test_wallet_binding_has_fixed_app_or_original_web_return(wallet,service,query,target):
    c,s,address=wallet
    response=c.post('/services/'+service+'/'+query,data={'csrf':'isolated-csrf','address':address},base_url='https://yidt.yitiao.org')
    assert response.status_code==302 and response.location==target
    with connect(s.ledger) as db:assert db.execute("SELECT count(*) FROM yidt_transactions WHERE kind='yichat_deposit'").fetchone()[0]==0


@pytest.mark.parametrize('client',['android','desktop'])
def test_pin_continuation_keeps_native_return_without_bypassing_verification(wallet,client):
    c,s,address=wallet
    with connect(s.ledger) as db:db.execute("DELETE FROM yidt_pins WHERE user='alice'")
    response=c.post('/services/yichat/?client='+client,data={'csrf':'isolated-csrf','address':address},base_url='https://yidt.yitiao.org')
    assert response.status_code==302 and response.location=='/pin/'
    with c.session_transaction(base_url='https://yidt.yitiao.org') as session:assert session['pin_return']=='/services/yichat/?client='+client
    response=c.post('/services/yichat/?client='+client,data={'csrf':'wrong','address':address},base_url='https://yidt.yitiao.org')
    assert response.status_code==403


@pytest.mark.parametrize('native,path,intent_on_approve',[
    ('desktop','/app/desktop-return?order=',False),('android','/app/return?order=',False),(None,'/orders/',True)])
@pytest.mark.parametrize('action',['approve','cancel'])
def test_authorize_returns_native_order_without_intent(wallet,env,monkeypatch,native,path,intent_on_approve,action):
    c,s,address=wallet;_,f,u=env
    monkeypatch.setattr(s,'origin','https://chat.yitiao.org')
    o=f.create(u['alice'],{'room':'!test:chat.yitiao.org','kind':'direct'},{'key':'authorize-'+action+'-'+str(native),'amount':'0.01'},'bob',native_return=native)
    with connect(s.ledger) as db:intent=db.execute('SELECT intent FROM yichat_orders WHERE id=?',(o['id'],)).fetchone()[0]
    expected='https://chat.yitiao.org'+path+o['id']
    if action=='cancel':r=c.post('/authorize/'+intent,data={'csrf':'isolated-csrf','action':'cancel'},base_url='https://yidt.yitiao.org')
    else:r=c.get('/authorize/'+intent,base_url='https://yidt.yitiao.org')
    assert r.status_code==302,r.get_data(as_text=True)[:300]
    if intent_on_approve and action=='approve':assert r.location==expected+'?yidt_intent='+intent
    else:assert r.location==expected and 'yidt_intent' not in r.location
    with connect(s.ledger) as db:state=db.execute('SELECT state FROM yidt_intents WHERE id=?',(intent,)).fetchone()[0]
    assert state==('cancelled' if action=='cancel' else 'approved')


def test_native_return_rejects_unknown_kind(env):
    s,f,u=env
    with pytest.raises(ValueError):
        f.create(u['alice'],{'room':'!test:chat.yitiao.org','kind':'direct'},{'key':'native-return-unknown-kind','amount':'0.01'},'bob',native_return=True)


def device(c,s,platform):
    """Mark alice's session as a native device; platform NULL models pre-migration rows."""
    c.cookies.clear();c.headers.pop('Origin',None);c.headers['Authorization']='Bearer session-token'
    with connect(s.db) as db:
        who=db.execute("SELECT * FROM sessions WHERE username='alice' LIMIT 1").fetchone()
        db.execute('INSERT INTO native_devices(proof_hash,subject,mxid,device,created,platform) VALUES(?,?,?,?,?,?)',
                   ('fixture-proof',who['subject'],who['mxid'],who['device'],time.time(),platform))

@pytest.mark.parametrize('platform,query',[
    ('web',''),(None,'?client=android'),('android','?client=android'),('windows','?client=desktop'),('linux','?client=desktop')])
def test_wallet_bind_url_follows_device_platform(gateway,platform,query):
    c,app,s=gateway
    if platform!='web':device(c,s,platform)
    assert c.get('/api/wallet').json()['bind_url']=='https://yidt.yitiao.org/services/yichat/'+query

@pytest.mark.parametrize('platform,path',[
    ('web','/orders/'),(None,'/app/return?order='),('android','/app/return?order='),
    ('windows','/app/desktop-return?order='),('linux','/app/desktop-return?order=')])
def test_order_return_url_follows_device_platform(gateway,platform,path):
    c,app,s=gateway;s.funds_enabled=True
    with connect(s.ledger) as db:
        for name,event in [('alice','$join1'),('bob','$join2')]:
            db.execute("INSERT OR IGNORE INTO yichat_memberships VALUES(?,?,'join',?,NULL,NULL,NULL)",('!direct','@'+name+':chat.yitiao.org',event))
    if platform!='web':device(c,s,platform)
    r=c.post('/api/orders',json={'room':'!direct','key':secrets.token_urlsafe(24),'amount':'0.01','count':1,'title':'test'})
    assert r.status_code==200,r.text
    oid=r.json()['id']
    with connect(s.ledger) as db:
        url=db.execute('SELECT i.return_url FROM yidt_intents i JOIN yichat_orders o ON o.intent=i.id WHERE o.id=?',(oid,)).fetchone()[0]
    assert url==s.origin+path+oid
