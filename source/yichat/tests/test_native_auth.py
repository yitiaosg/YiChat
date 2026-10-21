"""Signed isolated IDP transport; never contacts real accounts or Synapse."""
import base64,hashlib,json,secrets,time
from urllib.parse import urlsplit,parse_qs
from unittest.mock import AsyncMock
import httpx,jwt,pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import connect

@pytest.fixture
def native(env):
    s,_,users=env
    app=create_app(s)
    with TestClient(app) as c:
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));public['kid']='native-fixture'
        verifier=secrets.token_urlsafe(48);state=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32)
        params=dict(state=state,nonce=nonce,code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),code_challenge_method='S256',redirect_uri=s.origin+'/app/callback')
        app.state.matrix.provision=AsyncMock();app.state.matrix.login=AsyncMock(return_value={'access_token':'isolated-upstream','device_id':'NATIVE_DEVICE'})
        async def authorize(**overrides):
            start=c.get('/auth/native/login',params=params,follow_redirects=False)
            assert start.status_code==307,start.text
            q=parse_qs(urlsplit(start.headers['location']).query)
            claims=dict(iss=s.issuer,aud='yichat',sub=users['alice']['subject'],preferred_username='alice',sid='sid-alice',nonce=q['nonce'][0],iat=int(time.time()),exp=int(time.time()+300))|overrides
            encoded=jwt.encode(claims,key,algorithm='RS256',headers={'kid':'native-fixture'})
            def handler(r):
                if r.url.path=='/oidc/token':
                    form=parse_qs(r.content.decode());assert base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'][0].encode()).digest()).decode().rstrip('=')==q['code_challenge'][0]
                    return httpx.Response(200,json={'id_token':encoded})
                return httpx.Response(200,json={'keys':[public]})
            app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return c.get('/auth/callback',params={'state':q['state'][0],'code':'fixture'},follow_redirects=False)
        yield c,app,s,params,verifier,authorize

def exchange_data(n,r):
    c,app,s,p,v,_=n;q=parse_qs(urlsplit(r.headers['location']).query)
    return dict(code=q['code'][0],state=p['state'],nonce=p['nonce'],code_verifier=v,redirect_uri=p['redirect_uri'])

@pytest.mark.asyncio
async def test_native_signed_pkce_once_cookie_separation_and_sid(native):
    c,app,s,p,v,authorize=native
    c.cookies.set('yc_session','old-web-session')
    r=await authorize();assert r.status_code==303,r.text
    assert 'token' not in r.headers['location'] and 'sid' not in r.headers['location']
    assert c.cookies.get('yc_session')=='old-web-session'
    data=exchange_data(native,r);c.cookies.clear()
    result=c.post('/auth/native/exchange',json=data);assert result.status_code==200,result.text
    auth=result.json();assert auth['new_device'] and auth['device_id']=='NATIVE_DEVICE'
    assert c.post('/auth/native/exchange',json=data).status_code==400
    c.headers['Authorization']='Bearer '+auth['token']
    assert c.get('/api/session').json()['device']=='NATIVE_DEVICE'
    with connect(s.ledger) as db:db.execute("UPDATE yidt_sessions SET revoked=1 WHERE sid='sid-alice'")
    assert c.get('/api/session').status_code==401

@pytest.mark.parametrize('field,value',[('state','wrong'),('nonce','wrong'),('code_verifier','a'*64),('redirect_uri','https://evil.example/cb')])
@pytest.mark.asyncio
async def test_wrong_binding_rejected(native,field,value):
    c,app,s,p,v,authorize=native;r=await authorize();data=exchange_data(native,r);c.cookies.clear()
    assert c.post('/auth/native/exchange',json=data|{field:value}).status_code==400
    assert c.post('/auth/native/exchange',json=data).status_code==200

@pytest.mark.asyncio
async def test_native_expiration_and_revoked_sid(native):
    c,app,s,p,v,authorize=native;r=await authorize();data=exchange_data(native,r);c.cookies.clear()
    with connect(s.db) as db:db.execute('UPDATE native_codes SET expires=0')
    assert c.post('/auth/native/exchange',json=data).status_code==400

@pytest.mark.asyncio
async def test_restore_device_requires_possession_and_same_uid(native):
    c,app,s,p,v,authorize=native
    r=await authorize();data=exchange_data(native,r);c.cookies.clear()
    first=c.post('/auth/native/exchange',json=data).json()
    r=await authorize();data=exchange_data(native,r);c.cookies.clear()
    second=c.post('/auth/native/exchange',json=data|{'device_proof':first['device_proof']})
    assert second.status_code==200,second.text
    assert not second.json()['new_device']
    assert app.state.matrix.login.call_args.kwargs['device_id']=='NATIVE_DEVICE'
    assert second.json()['device_proof']==first['device_proof']

@pytest.mark.parametrize('claim,value',[('nonce','evil'),('sub','0'*32),('sid','revoked'),('aud','wrong'),('iss','https://evil.example')])
@pytest.mark.asyncio
async def test_signed_claims_are_verified(native,claim,value):
    c,app,s,p,v,authorize=native
    r=await authorize(**{claim:value});assert r.status_code==401
    with connect(s.db) as db:assert db.execute('SELECT count(*) FROM native_codes').fetchone()[0]==0

@pytest.mark.parametrize('params',[{'redirect_uri':'https://evil.example/cb'},{'code_challenge_method':'plain'},{'state':'short'},{'extra':'x'}])
def test_fixed_redirect_and_entropy(native,params):
    c,app,s,p,v,authorize=native
    assert c.get('/auth/native/login',params=p|params,follow_redirects=False).status_code==400

@pytest.mark.parametrize('headers',[{'Origin':'https://evil.example'},{'Sec-Fetch-Mode':'cors'}])
def test_native_exchange_rejects_browser(native,headers):
    c,*_=native
    assert c.post('/auth/native/exchange',json={},headers=headers).status_code==403

def test_uninstalled_fallback_never_reflects_code(native):
    c,*_=native
    r=c.get('/app/callback?code=secret&state=secret')
    assert r.status_code==200 and 'secret' not in r.text and r.headers['Referrer-Policy']=='no-referrer'

def test_login_return_page_and_script_without_database():
    from fastapi import FastAPI
    from types import SimpleNamespace
    from backend.native_auth import install
    app=FastAPI()
    install(app,SimpleNamespace(origin='https://chat.yitiao.org'))
    with TestClient(app) as client:
        page=client.get('/app/callback?code=untrusted-code&state=%3Cscript%3E')
        assert page.status_code==200
        assert 'untrusted-code' not in page.text and '<script>' not in page.text
        assert 'id="return-app"' in page.text and '/app/native-return.js' in page.text
        script=client.get('/app/native-return.js')
        assert script.status_code==200 and script.headers['content-type'].startswith('application/javascript')
        assert 'package=org.yitiao.yichat.mobile;end' in script.text
        assert 'getAll("code").length === 1' in script.text

def test_native_login_rejects_duplicate_query_fields(native):
    c,app,s,p,v,authorize=native
    duplicated=list(p.items())+[('state','x'*64)]
    assert c.get('/auth/native/login',params=duplicated,follow_redirects=False).status_code==400

def test_android_assetlinks_public_fixed_package_and_certificate(native):
    c,*_=native
    c.cookies.clear()
    response=c.get('/.well-known/assetlinks.json',follow_redirects=False)
    assert response.status_code==200
    assert response.headers['content-type']=='application/json'
    assert 'max-age=3600' in response.headers['cache-control']
    target=response.json()[0]['target']
    assert target['package_name']=='org.yitiao.yichat'
    assert target['sha256_cert_fingerprints']==['6A:B2:1F:96:1A:37:35:69:C0:67:E0:48:E0:E0:E5:D3:8B:49:0D:07:D9:40:B9:74:A4:A3:29:05:F0:62:85:ED']
