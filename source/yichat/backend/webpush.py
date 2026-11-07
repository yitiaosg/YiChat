"""Standard Web Push (RFC 8030/8291/8292) for the Web version only.

Subscriptions are bound to a browser session of the Web version; App devices
keep using FCM/their own notifications and cannot register here. The payload
(sender + bounded preview, same rules as the Android preview) is encrypted to
the browser; the push service only relays ciphertext. Endpoints are restricted
to the major browser push services so the gateway cannot be used for SSRF.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit
import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from fastapi.responses import FileResponse
from .db import connect, transaction
from .security import authenticate, digest, fail, valid_sid
from .conversations import notification_hints
from .notification_summary import event_preview

log=logging.getLogger('yichat.webpush')
SCHEMA='''CREATE TABLE IF NOT EXISTS webpush_subscriptions(
 endpoint_hash TEXT PRIMARY KEY,subject TEXT NOT NULL,session_hash TEXT NOT NULL,data TEXT NOT NULL,
 created REAL NOT NULL,cursor INTEGER NOT NULL,lease TEXT,lease_until REAL NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS webpush_subject ON webpush_subscriptions(subject);'''
MAX_PER_ACCOUNT=10
# Push services of Chrome/Edge (FCM), Firefox, Safari and legacy Edge (WNS).
HOSTS=('fcm.googleapis.com','updates.push.services.mozilla.com')
SUFFIXES=('.push.apple.com','.notify.windows.com')
WEB_ONLY={'code':'YICHAT_WEB_ONLY','error':'浏览器通知仅供 YiChat 网页版使用；App 请使用 App 内的通知设置'}


def b64d(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}',value):raise ValueError('base64url')
    return base64.urlsafe_b64decode(value+'='*(-len(value)%4))


def b64e(data):return base64.urlsafe_b64encode(data).decode().rstrip('=')


def valid_endpoint(endpoint):
    if not isinstance(endpoint,str) or len(endpoint)>1024:return False
    try:u=urlsplit(endpoint)
    except ValueError:return False
    host=(u.hostname or '').lower()
    try:port=u.port
    except ValueError:return False
    return (u.scheme=='https' and not u.username and not u.password and port in (None,443) and not u.fragment
            and (host in HOSTS or any(host.endswith(x) and len(host)>len(x) for x in SUFFIXES)))


def load_vapid(path):
    """Load the gateway VAPID key, creating it once (0600) if absent."""
    path=Path(path)
    if not path.exists():
        key=ec.generate_private_key(ec.SECP256R1())
        pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(pem)
        except FileExistsError:pass
    key=serialization.load_pem_private_key(path.read_bytes(),password=None)
    if not isinstance(key,ec.EllipticCurvePrivateKey) or key.curve.name!='secp256r1':raise ValueError('VAPID key must be P-256')
    return key


def public_raw(key):
    return key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)


def _hkdf(salt,ikm,info,length):
    prk=hmac.new(salt,ikm,hashlib.sha256).digest()
    return hmac.new(prk,info+b'\x01',hashlib.sha256).digest()[:length]


def encrypt(p256dh,auth,plaintext,salt=None,server_key=None):
    """RFC 8291 aes128gcm, single record."""
    ua_public=b64d(p256dh);secret=b64d(auth)
    if len(ua_public)!=65 or ua_public[0]!=4 or len(secret)!=16:raise ValueError('Invalid subscription keys')
    server_key=server_key or ec.generate_private_key(ec.SECP256R1())
    as_public=public_raw(server_key)
    shared=server_key.exchange(ec.ECDH(),ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),ua_public))
    ikm=_hkdf(secret,shared,b'WebPush: info\x00'+ua_public+as_public,32)
    salt=salt or os.urandom(16)
    cek=_hkdf(salt,ikm,b'Content-Encoding: aes128gcm\x00',16)
    nonce=_hkdf(salt,ikm,b'Content-Encoding: nonce\x00',12)
    if len(plaintext)>3800:raise ValueError('Payload too large')
    body=AESGCM(cek).encrypt(nonce,plaintext+b'\x02',None)
    return salt+struct.pack('!IB',4096,len(as_public))+as_public+body


def vapid_header(key,endpoint,subject):
    u=urlsplit(endpoint)
    token=jwt.encode({'aud':u.scheme+'://'+u.netloc,'exp':int(time.time())+12*3600,'sub':subject},key,algorithm='ES256')
    return 'vapid t='+token+', k='+b64e(public_raw(key))


class WebPushSender:
    def __init__(self,settings,http):
        self.s=settings;self.http=http
        self.cipher=Fernet(settings.session_key.encode())
        self.key=load_vapid(settings.webpush_key)
    @property
    def public_key(self):return b64e(public_raw(self.key))

    def describe(self,session,room):
        """Title/body: sender and the bounded preview (legacy encrypted: fixed text)."""
        with connect(self.s.db) as c:
            receipt=c.execute('SELECT sender FROM history_receipts WHERE room=? AND event_id=?',(room['room'],room['event'])).fetchone()
            sender=c.execute('SELECT username FROM identities WHERE mxid=?',(receipt['sender'],)).fetchone() if receipt else None
        name=sender['username'] if sender else '新消息'
        try:preview=event_preview(self.s,session,room)
        except Exception:
            log.warning('Web Push preview unavailable; sending fixed text')
            preview=''
        preview=preview or '你有新的消息'
        return {'title':name+('（群聊）' if room['kind']=='group' else ''),'body':preview}

    async def send(self,row,payload):
        sub=json.loads(self.cipher.decrypt(row['data'].encode()))
        body=encrypt(sub['p256dh'],sub['auth'],json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode())
        headers={'Authorization':vapid_header(self.key,sub['endpoint'],self.s.origin),'TTL':'86400','Urgency':'high',
                 'Content-Encoding':'aes128gcm','Content-Type':'application/octet-stream','Topic':digest(payload['room'])[:32]}
        response=await self.http.post(sub['endpoint'],content=body,headers=headers,timeout=15)
        # Endpoint URLs are capabilities: never log them or response bodies.
        if response.status_code in (404,410):return 'expired'
        if response.status_code in (400,403,413):
            log.warning('Web Push rejected by push service (%s)',response.status_code);return 'expired' if response.status_code==403 else 'sent'
        if response.status_code not in (200,201,202):raise RuntimeError('Web Push delivery unavailable')
        return 'sent'

    async def drain(self):
        now=time.time();lease=secrets.token_hex(16)
        with transaction(self.s.db) as c:
            row=c.execute('SELECT * FROM webpush_subscriptions WHERE lease_until<? AND next_attempt<=? ORDER BY next_attempt,endpoint_hash LIMIT 1',(now,now)).fetchone()
            if not row:return False
            c.execute('UPDATE webpush_subscriptions SET lease=?,lease_until=? WHERE endpoint_hash=?',(lease,now+90,row['endpoint_hash']))
        try:
            with connect(self.s.db) as c:session=c.execute('SELECT * FROM sessions WHERE token_hash=? AND revoked=0 AND expires>?',(row['session_hash'],time.time())).fetchone()
            if not session or not valid_sid(self.s,session['sid'],session['username']):
                with transaction(self.s.db) as c:c.execute('DELETE FROM webpush_subscriptions WHERE lease=?',(lease,))
                return True
            session=dict(session)
            result=notification_hints(self.s,session,row['cursor'])
            for room in result['rooms']:
                with connect(self.s.db) as c:
                    preference=c.execute('SELECT archived,muted,read_sequence FROM conversation_preferences WHERE subject=? AND room=?',(row['subject'],room['room'])).fetchone()
                    live=c.execute('SELECT 1 FROM webpush_subscriptions w JOIN sessions s ON s.token_hash=w.session_hash WHERE w.lease=? AND s.revoked=0 AND s.expires>?',(lease,time.time())).fetchone()
                if not live:break
                if preference and (preference['archived'] or preference['muted'] or preference['read_sequence']>=int(room['sequence'])):continue
                text=await asyncio.to_thread(self.describe,session,room)
                if await self.send(row,{'room':room['room'],'event':room['event'],'count':room['count'],**text})=='expired':
                    with transaction(self.s.db) as c:c.execute('DELETE FROM webpush_subscriptions WHERE lease=?',(lease,))
                    return True
            with transaction(self.s.db) as c:
                c.execute('UPDATE webpush_subscriptions SET cursor=?,lease=NULL,lease_until=0,failures=0,next_attempt=? WHERE lease=?',
                          (int(result['cursor']),time.time()+(0 if result.get('has_more') else 2),lease))
        except asyncio.CancelledError:raise
        except Exception:
            log.warning('Web Push batch pending; cursor retained')
            with transaction(self.s.db) as c:
                c.execute('UPDATE webpush_subscriptions SET lease=NULL,lease_until=0,failures=failures+1,next_attempt=? WHERE lease=?',
                          (time.time()+min(300,2**min(row['failures']+1,8)),lease))
        return True

    async def run(self):
        while True:
            try:await self.drain()
            except asyncio.CancelledError:raise
            except Exception:log.warning('Web Push dispatch unavailable')
            await asyncio.sleep(.5)


def install(app,s):
    def web_session(request):
        # Same rule as API key management: Web version browser session only.
        cookie=request.cookies.get('yc_session','')
        bearer=request.headers.get('authorization','')
        if not cookie or (bearer and bearer!='Bearer '+cookie):fail(403,WEB_ONLY)
        if request.headers.get('sec-fetch-site') not in (None,'same-origin'):fail(403,WEB_ONLY)
        who=authenticate(s,cookie)
        with connect(s.db) as c:
            if c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=? LIMIT 1',(who['subject'],who['device'])).fetchone():fail(403,WEB_ONLY)
        return who,cookie

    def sender():
        if not getattr(app.state,'webpush',None):fail(503,'浏览器通知暂不可用')
        return app.state.webpush

    @app.get('/api/webpush/config')
    async def config(request:Request):
        web_session(request)
        return {'public_key':sender().public_key}

    @app.post('/api/webpush/subscriptions')
    async def subscribe(request:Request):
        who,cookie=web_session(request);push=sender()
        try:data=await request.json()
        except ValueError:fail(400,'JSON 无效')
        keys=data.get('keys') if isinstance(data,dict) else None
        if not isinstance(data,dict) or not valid_endpoint(data.get('endpoint')) or not isinstance(keys,dict):fail(400,'不支持的推送订阅')
        try:
            ok=len(b64d(keys.get('p256dh')))==65 and len(b64d(keys.get('auth')))==16
        except (ValueError,TypeError):ok=False
        if not ok:fail(400,'推送订阅密钥无效')
        blob=push.cipher.encrypt(json.dumps({'endpoint':data['endpoint'],'p256dh':keys['p256dh'],'auth':keys['auth']}).encode()).decode()
        key=digest(data['endpoint']);start=int(notification_hints(s,who,None)['cursor'])
        with transaction(s.db) as c:
            old=c.execute('SELECT subject FROM webpush_subscriptions WHERE endpoint_hash=?',(key,)).fetchone()
            if old and old['subject']!=who['subject']:c.execute('DELETE FROM webpush_subscriptions WHERE endpoint_hash=?',(key,));old=None
            if not old and c.execute('SELECT count(*) FROM webpush_subscriptions WHERE subject=?',(who['subject'],)).fetchone()[0]>=MAX_PER_ACCOUNT:
                fail(409,{'code':'YICHAT_WEBPUSH_LIMIT','error':'此账户已在 10 个浏览器开启通知，请先在其他浏览器关闭'})
            if old:
                # Rebind to the current browser session (e.g. after re-login).
                c.execute('UPDATE webpush_subscriptions SET session_hash=?,data=? WHERE endpoint_hash=?',(digest(cookie),blob,key))
            else:
                c.execute('INSERT INTO webpush_subscriptions(endpoint_hash,subject,session_hash,data,created,cursor) VALUES(?,?,?,?,?,?)',
                          (key,who['subject'],digest(cookie),blob,time.time(),start))
        return {'ok':True}

    @app.post('/api/webpush/subscriptions/remove')
    async def unsubscribe(request:Request):
        who,_=web_session(request)
        try:data=await request.json()
        except ValueError:fail(400,'JSON 无效')
        endpoint=data.get('endpoint') if isinstance(data,dict) else None
        if not isinstance(endpoint,str) or len(endpoint)>1024:fail(400,'推送订阅无效')
        with transaction(s.db) as c:c.execute('DELETE FROM webpush_subscriptions WHERE endpoint_hash=? AND subject=?',(digest(endpoint),who['subject']))
        return {'ok':True}

    @app.get('/push/sw.js')
    async def push_worker():
        # Separate scope (/push/): it never intercepts page requests and does
        # not conflict with the retired /sw.js worker.
        return FileResponse(Path(__file__).parent.parent/'web'/'push-sw.js',media_type='application/javascript')
