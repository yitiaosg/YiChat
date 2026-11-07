"""Web Push: RFC 8291 encryption, VAPID, web-only subscriptions and delivery.
Push services are mocked; no real browser endpoint is contacted."""
import base64,json,os,secrets,time
import httpx,jwt,pytest
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient
from backend import webpush
from backend.db import connect
from test_gateway import gateway
from test_open_api import put,text,mxid


def b64(data):return base64.urlsafe_b64encode(data).decode().rstrip('=')


class Browser:
    """Receiving side of a push subscription, implemented independently."""
    def __init__(self):
        self.key=ec.generate_private_key(ec.SECP256R1());self.auth=os.urandom(16)
        self.public=self.key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    def subscription(self,endpoint='https://fcm.googleapis.com/fcm/send/'+'a'*40):
        return {'endpoint':endpoint,'keys':{'p256dh':b64(self.public),'auth':b64(self.auth)}}
    def decrypt(self,body):
        salt,rs,idlen=body[:16],int.from_bytes(body[16:20],'big'),body[20]
        sender=body[21:21+idlen];ciphertext=body[21+idlen:]
        assert rs==4096 and idlen==65
        shared=self.key.exchange(ec.ECDH(),ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),sender))
        ikm=HKDF(hashes.SHA256(),32,self.auth,b'WebPush: info\x00'+self.public+sender).derive(shared)
        cek=HKDF(hashes.SHA256(),16,salt,b'Content-Encoding: aes128gcm\x00').derive(ikm)
        nonce=HKDF(hashes.SHA256(),12,salt,b'Content-Encoding: nonce\x00').derive(ikm)
        plain=AESGCM(cek).decrypt(nonce,ciphertext,None)
        assert plain.endswith(b'\x02')
        return plain[:-1]


def test_encrypt_matches_independent_receiver():
    browser=Browser()
    body=webpush.encrypt(b64(browser.public),b64(browser.auth),'{"title":"你好"}'.encode())
    assert browser.decrypt(body)=='{"title":"你好"}'.encode()
    # Fresh ephemeral key and salt per message.
    assert body[:16]!=webpush.encrypt(b64(browser.public),b64(browser.auth),b'x')[:16]


@pytest.mark.parametrize('p256dh,auth',[('AAAA',b64(os.urandom(16))),(b64(b'\x04'+os.urandom(64)),b64(os.urandom(8))),('not base64!','x')])
def test_encrypt_rejects_bad_keys(p256dh,auth):
    with pytest.raises(ValueError):webpush.encrypt(p256dh,auth,b'x')


def test_vapid_header_signed_for_endpoint_origin(tmp_path):
    key=webpush.load_vapid(tmp_path/'v.pem')
    assert oct((tmp_path/'v.pem').stat().st_mode&0o777)=='0o600'
    assert webpush.load_vapid(tmp_path/'v.pem').private_numbers().private_value==key.private_numbers().private_value
    header=webpush.vapid_header(key,'https://fcm.googleapis.com/fcm/send/abc','https://chat.yitiao.org')
    token=header.split('t=')[1].split(',')[0];k=header.split('k=')[1]
    claims=jwt.decode(token,key.public_key(),algorithms=['ES256'],audience='https://fcm.googleapis.com')
    assert claims['sub']=='https://chat.yitiao.org' and claims['exp']<=time.time()+12*3600+5
    assert k==b64(webpush.public_raw(key))


@pytest.mark.parametrize('endpoint,ok',[
    ('https://fcm.googleapis.com/fcm/send/abc',True),('https://updates.push.services.mozilla.com/wpush/v2/x',True),
    ('https://web.push.apple.com/QOx',True),('https://wns2-par02p.notify.windows.com/w/?token=x',True),
    ('http://fcm.googleapis.com/fcm/send/abc',False),('https://fcm.googleapis.com:8443/x',False),
    ('https://user@fcm.googleapis.com/x',False),('https://evil.example/x',False),('https://push.apple.com.evil.example/x',False),
    ('https://.push.apple.com/x',False),('https://127.0.0.1/x',False),('https://fcm.googleapis.com/x#f',False),(None,False),('x'*2000,False)])
def test_endpoint_allowlist(endpoint,ok):
    assert webpush.valid_endpoint(endpoint) is ok


def test_subscription_is_web_only_and_validated(gateway):
    c,app,s=gateway;browser=Browser()
    assert c.get('/api/webpush/config').json()['public_key']==app.state.webpush.public_key
    app_style=TestClient(app);app_style.headers['Authorization']='Bearer session-token'
    r=app_style.post('/api/webpush/subscriptions',json=browser.subscription())
    assert r.status_code==403 and r.json()['code']=='YICHAT_WEB_ONLY'
    assert c.post('/api/webpush/subscriptions',json=browser.subscription('https://evil.example/x')).status_code==400
    bad=browser.subscription();bad['keys']['auth']=b64(os.urandom(4))
    assert c.post('/api/webpush/subscriptions',json=bad).status_code==400
    assert c.post('/api/webpush/subscriptions',json=browser.subscription()).status_code==200
    assert c.post('/api/webpush/subscriptions',json=browser.subscription()).status_code==200
    with connect(s.db) as db:
        rows=db.execute('SELECT * FROM webpush_subscriptions').fetchall()
        assert len(rows)==1 and 'fcm.googleapis.com' not in rows[0]['data']
    assert c.post('/api/webpush/subscriptions/remove',json={'endpoint':browser.subscription()['endpoint']}).status_code==200
    with connect(s.db) as db:assert not db.execute('SELECT 1 FROM webpush_subscriptions').fetchone()


def capture(app,status=201):
    seen=[]
    def handler(request):
        seen.append(request);return httpx.Response(status)
    app.state.webpush.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen


def wait(seen,count=1,timeout=8):
    end=time.time()+timeout
    while len(seen)<count and time.time()<end:time.sleep(.1)
    return seen


def test_delivers_sender_and_preview_encrypted(gateway):
    c,app,s=gateway;browser=Browser();seen=capture(app)
    assert c.post('/api/webpush/subscriptions',json=browser.subscription()).status_code==200
    put(s,[text('$push','!direct',50,int(time.time()*1000),mxid(s,'bob'),'晚上一起吃饭？')])
    request=wait(seen)[0]
    assert str(request.url)==browser.subscription()['endpoint']
    assert request.headers['content-encoding']=='aes128gcm' and request.headers['ttl']=='86400'
    assert request.headers['authorization'].startswith('vapid t=') and len(request.headers['topic'])==32
    payload=json.loads(browser.decrypt(request.content))
    assert payload['title']=='bob' and payload['body']=='晚上一起吃饭？' and payload['room']=='!direct' and payload['event']=='$push'


def test_muted_and_own_messages_are_not_pushed_and_gone_endpoint_is_removed(gateway):
    c,app,s=gateway;browser=Browser();seen=capture(app,status=410)
    assert c.post('/api/webpush/subscriptions',json=browser.subscription()).status_code==200
    with connect(s.db) as db:db.execute("INSERT INTO conversation_preferences(subject,room,muted) VALUES((SELECT subject FROM identities WHERE username='alice'),'!group',1)")
    now=int(time.time()*1000)
    put(s,[text('$own','!direct',60,now,mxid(s,'alice'),'mine'),text('$muted','!group',61,now,mxid(s,'bob'),'muted')])
    time.sleep(3);assert seen==[]
    put(s,[text('$gone','!direct',62,now,mxid(s,'bob'),'hello')])
    wait(seen)
    end=time.time()+5
    while time.time()<end:
        with connect(s.db) as db:
            if not db.execute('SELECT 1 FROM webpush_subscriptions').fetchone():break
        time.sleep(.1)
    with connect(s.db) as db:assert not db.execute('SELECT 1 FROM webpush_subscriptions').fetchone()


def test_revoked_session_stops_delivery(gateway):
    c,app,s=gateway;browser=Browser();seen=capture(app)
    assert c.post('/api/webpush/subscriptions',json=browser.subscription()).status_code==200
    with connect(s.db) as db:db.execute("UPDATE sessions SET revoked=1 WHERE username='alice'")
    put(s,[text('$after','!direct',70,int(time.time()*1000),mxid(s,'bob'),'x')])
    time.sleep(3)
    assert seen==[]
    with connect(s.db) as db:assert not db.execute('SELECT 1 FROM webpush_subscriptions').fetchone()


def test_worker_script_is_served_without_fetch_handler(gateway):
    c,app,s=gateway
    r=c.get('/push/sw.js')
    assert r.status_code==200 and r.headers['content-type'].startswith('application/javascript')
    assert "addEventListener('push'" in r.text and "addEventListener('fetch'" not in r.text and 'caches.' not in r.text


SW_RUNNER=r"""
const vm=require('vm');let input='';process.stdin.on('data',d=>input+=d).on('end',async()=>{
  const {script,cases}=JSON.parse(input);const out=[];
  for(const c of cases){
    const listeners={},shown=[],opened=[],focused=[],posted=[];
    const windows=c.windows.map(url=>({url,focus:async()=>focused.push(url),postMessage:m=>posted.push(m)}));
    const self={location:{origin:'https://chat.yitiao.org'},addEventListener:(t,f)=>listeners[t]=f,skipWaiting(){},
      registration:{showNotification:async(title,opt)=>shown.push({title,...opt})},
      clients:{claim:async()=>{},matchAll:async()=>windows,openWindow:async u=>opened.push(u)}};
    vm.runInNewContext(script,{self,URL,console});
    let waits=[];
    if(c.push!==undefined)listeners.push({data:{json:()=>{if(c.push==='bad')throw Error('x');return c.push}},waitUntil:p=>waits.push(p)});
    await Promise.all(waits);waits=[];
    if(c.click!==undefined)listeners.notificationclick({notification:{data:c.click,close(){}},waitUntil:p=>waits.push(p)});
    await Promise.all(waits);
    out.push({shown,opened,focused,posted,hasFetch:'fetch' in listeners});
  }
  process.stdout.write(JSON.stringify(out));
});
"""


@pytest.mark.skipif(__import__('shutil').which('node') is None,reason='node not installed')
def test_worker_shows_and_routes_notifications():
    import shutil,subprocess
    from pathlib import Path
    script=(Path(__file__).resolve().parents[1]/'web'/'push-sw.js').read_text()
    room='!abc:chat.yitiao.org'
    cases=[
        {'windows':[],'push':{'room':room,'title':'bob','body':'hi'}},
        {'windows':[],'push':'bad'},
        {'windows':[],'push':{'room':'<script>','title':'x'*300,'body':'y'*999}},
        {'windows':['https://chat.yitiao.org/'],'click':{'room':room}},
        {'windows':[],'click':{'room':room}},
        {'windows':['https://evil.example/'],'click':{'room':'javascript:alert(1)'}},
    ]
    run=subprocess.run([shutil.which('node'),'-e',SW_RUNNER],input=json.dumps({'script':script,'cases':cases}),capture_output=True,text=True,timeout=30,check=True)
    r=json.loads(run.stdout)
    assert r[0]['shown']==[{'title':'bob','body':'hi','tag':room,'renotify':True,'icon':'/brand/yichat.png','badge':'/brand/yichat.png','data':{'room':room}}]
    assert r[1]['shown'][0]['title']=='YiChat' and r[1]['shown'][0]['body']=='你有新的消息'
    assert r[2]['shown'][0]['data']=={'room':''} and len(r[2]['shown'][0]['title'])==100 and len(r[2]['shown'][0]['body'])==300
    assert r[3]['focused']==['https://chat.yitiao.org/'] and r[3]['posted']==[{'type':'yc:open-room','room':room}] and r[3]['opened']==[]
    assert r[4]['opened']==['/?room=!abc%3Achat.yitiao.org']  # encodeURIComponent
    assert r[5]['opened']==['/'] and r[5]['focused']==[]
    assert not any(x['hasFetch'] for x in r)
