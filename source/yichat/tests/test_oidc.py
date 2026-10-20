import time,json,secrets
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt,httpx
from backend.app import create_app
from backend.db import connect
from backend.security import digest
from unittest.mock import AsyncMock

def test_real_signed_oidc_callback_pkce_state_nonce_and_replay(env):
    s,f,u=env;s.funds_enabled=False
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()));public['kid']='fixture';public['alg']='RS256'
    app=create_app(s)
    with TestClient(app) as c:
        start=c.get('/auth/login',follow_redirects=False);assert start.status_code==307
        from urllib.parse import urlsplit,parse_qs
        args=parse_qs(urlsplit(start.headers['location']).query)
        assert args['code_challenge_method']==['S256'];assert args['client_id']==['yichat']
        state=args['state'][0]
        claims=dict(iss=s.issuer,aud='yichat',sub=u['alice']['subject'],preferred_username='alice',sid='sid-alice',nonce=args['nonce'][0],iat=int(time.time()),exp=int(time.time()+300))
        encoded=jwt.encode(claims,private,algorithm='RS256',headers={'kid':'fixture'})
        calls=[]
        def handler(request):
            calls.append(str(request.url))
            if request.url.path=='/oidc/token':
                from urllib.parse import parse_qs
                from hashlib import sha256
                import base64
                form=parse_qs(request.content.decode());challenge=base64.urlsafe_b64encode(sha256(form['code_verifier'][0].encode()).digest()).decode().rstrip('=')
                assert challenge==args['code_challenge'][0]
                return httpx.Response(200,json={'id_token':encoded})
            return httpx.Response(200,json={'keys':[public]})
        app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app.state.matrix.provision=AsyncMock();app.state.matrix.login=AsyncMock(return_value={'access_token':'upstream','device_id':'device'})
        wrong=c.get('/auth/callback?state=wrong&code=x');assert wrong.status_code==400
        good=c.get('/auth/callback?state='+state+'&code=x',follow_redirects=False);assert good.status_code==307,good.text
        assert c.get('/api/session').json()['user']=='alice'
        assert c.get('/auth/callback?state='+state+'&code=x').status_code==400
        with connect(s.ledger) as db:db.execute("UPDATE yidt_sessions SET revoked=1 WHERE user='alice'")
        assert c.get('/api/session').status_code==401

def test_callback_rejects_wrong_nonce(env):
    s,f,u=env;s.funds_enabled=False
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048);public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));public['kid']='test'
    app=create_app(s)
    with TestClient(app) as c:
        from urllib.parse import urlsplit,parse_qs
        r=c.get('/auth/login',follow_redirects=False);args=parse_qs(urlsplit(r.headers['location']).query)
        token=jwt.encode(dict(iss=s.issuer,aud='yichat',sub=u['alice']['subject'],preferred_username='alice',sid='sid-alice',nonce='wrong',iat=time.time(),exp=time.time()+300),key,algorithm='RS256',headers={'kid':'test'})
        def handler(r):return httpx.Response(200,json={'id_token':token} if r.url.path=='/oidc/token' else {'keys':[public]})
        app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert c.get('/auth/callback?state='+args['state'][0]+'&code=test').status_code==401
