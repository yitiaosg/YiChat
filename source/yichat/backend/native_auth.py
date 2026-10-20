"""Gateway-native handoff; deliberately not an OIDC public client endpoint.

Only one fixed HTTPS redirect. No gateway/upstream token ever enters a URL.
The existing callback verifies the upstream signature, UID, nonce and SID first.
"""
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse, HTMLResponse
from .db import transaction
from .security import digest, fail, valid_sid
from . import policy

OPAQUE=re.compile(r'[A-Za-z0-9_-]{43,128}\Z')
VERIFIER=re.compile(r'[A-Za-z0-9._~-]{43,128}\Z')

def s256(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')

def finish_authorization(s,native,claims,person):
    code=secrets.token_urlsafe(32)
    with transaction(s.db) as c:
        c.execute('DELETE FROM native_codes WHERE expires<=?',(time.time(),))
        c.execute('INSERT INTO native_codes VALUES(?,?,?,?,?,?,?,?)',(
            digest(code),native['app_state'],native['app_nonce'],native['challenge'],
            claims['sub'],person['username'],claims['sid'],time.time()+60,
        ))
    r=RedirectResponse(s.origin+'/app/callback?'+urlencode({'code':code,'state':native['app_state']}),status_code=303)
    r.delete_cookie('yc_login')
    return r

def install(app,s):
    @app.get('/auth/native/login')
    async def start(request:Request):
        q=request.query_params
        if set(q)!={'state','nonce','code_challenge','code_challenge_method','redirect_uri'}:fail(400,'原生登录参数不完整')
        if q['redirect_uri']!=s.origin+'/app/callback' or q['code_challenge_method']!='S256':fail(400,'回跳或 PKCE 方法不匹配')
        if not all(OPAQUE.fullmatch(q[k]) for k in ('state','nonce','code_challenge')) or len(q['code_challenge'])!=43:fail(400,'登录随机参数无效')
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48);nonce=secrets.token_urlsafe(32)
        with transaction(s.db) as c:
            c.execute('DELETE FROM logins WHERE expires<=?',(time.time(),))
            c.execute('INSERT INTO logins VALUES(?,?,?,?)',(digest(state),verifier,nonce,time.time()+600))
            c.execute('INSERT INTO native_logins VALUES(?,?,?,?)',(digest(state),q['state'],q['nonce'],q['code_challenge']))
        params=dict(client_id='yichat',response_type='code',scope='openid profile',redirect_uri=s.origin+'/auth/callback',
                    state=state,nonce=nonce,code_challenge=s256(verifier),code_challenge_method='S256')
        r=RedirectResponse(s.issuer+'/oidc/authorize?'+urlencode(params))
        r.set_cookie('yc_login',state,secure=s.origin.startswith('https:'),httponly=True,samesite='lax',max_age=600)
        return r

    @app.post('/auth/native/exchange')
    async def exchange(request:Request):
        try:d=await request.json()
        except ValueError:fail(400,'JSON 无效')
        if not isinstance(d,dict):fail(400,'JSON 无效')
        if not all(isinstance(d.get(k),str) for k in ('code','state','nonce','code_verifier','redirect_uri')):fail(400,'交换参数不完整')
        if not OPAQUE.fullmatch(d['code']) or not VERIFIER.fullmatch(d['code_verifier']) or d['redirect_uri']!=s.origin+'/app/callback':fail(400,'交换参数无效')
        with transaction(s.db) as c:
            row=c.execute('SELECT * FROM native_codes WHERE code_hash=? AND expires>?',(digest(d['code']),time.time())).fetchone()
            if not row or not all(secrets.compare_digest(row[k],v) for k,v in [('app_state',d['state']),('app_nonce',d['nonce']),('challenge',s256(d['code_verifier']))]):fail(400,'交换码过期、已使用或校验失败')
            if not valid_sid(s,row['sid'],row['username']):fail(401,'主站会话已退出')
            person=policy.identity(s,row['username'])
            if row['subject']!=person['uid']:fail(401,'账户身份已改变')
            c.execute('DELETE FROM native_codes WHERE code_hash=?',(row['code_hash'],))
            resume=None
            proof=d.get('device_proof')
            if proof:
                if not isinstance(proof,str) or not OPAQUE.fullmatch(proof):fail(400,'设备恢复凭据无效')
                resume=c.execute('SELECT * FROM native_devices WHERE proof_hash=? AND subject=?',(digest(proof),row['subject'])).fetchone()
                if not resume:fail(403,'设备恢复凭据与已登录账户不匹配')
        username=row['username'];subject=row['subject'];mxid=policy.mxid(s,username);cipher=app.state.cipher
        with transaction(s.db) as c:
            c.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?,?)',(subject,username,mxid,cipher.encrypt(secrets.token_urlsafe(48).encode()).decode()))
            identity=c.execute('SELECT * FROM identities WHERE subject=?',(subject,)).fetchone()
        if resume and resume['mxid']!=mxid:fail(403,'设备身份不匹配')
        password=cipher.decrypt(identity['password'].encode()).decode()
        m=app.state.matrix
        await m.provision(mxid,password,username)
        auth=await m.login(mxid,password,device_id=resume['device'] if resume else None)
        if resume and auth['device_id']!=resume['device']:fail(503,'服务器未恢复原设备，拒绝打开原密钥库')
        token=secrets.token_urlsafe(48);proof=proof or secrets.token_urlsafe(48)
        expires=time.time()+30*86400
        with transaction(s.db) as c:
            if not valid_sid(s,row['sid'],username):fail(401,'主站会话已退出')
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),subject,username,row['sid'],mxid,auth['device_id'],cipher.encrypt(auth['access_token'].encode()).decode(),expires))
            if not resume:c.execute('INSERT INTO native_devices VALUES(?,?,?,?,?)',(digest(proof),subject,mxid,auth['device_id'],time.time()))
        return {'uid':subject,'user':username,'mxid':mxid,'device_id':auth['device_id'],'token':token,'expires':expires,
                'device_proof':proof,'new_device':not bool(resume),'homeserver':s.origin}

    @app.get('/app/callback')
    @app.get('/app/return')
    async def fallback():
        # No JS, no rendering of query strings, no third-party resources or secrets.
        return HTMLResponse('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>返回 YiChat</title><h1>返回 YiChat App</h1><p>请打开已安装的 YiChat 完成操作。如未安装，请先安装应用后重新登录。交换码将在60秒后失效。</p><a href="/">打开网页版</a></html>')
