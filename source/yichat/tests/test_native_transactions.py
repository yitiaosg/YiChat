import json,time
import httpx,pytest
from backend.db import connect
from test_gateway import gateway

def native(c,s):
    c.cookies.clear();c.headers.pop('Origin',None);c.headers['Authorization']='Bearer session-token'
    with connect(s.db) as db:
        who=db.execute("SELECT * FROM sessions WHERE username='alice' LIMIT 1").fetchone()
        db.execute('INSERT INTO native_devices VALUES(?,?,?,?,?)',('fixture-proof',who['subject'],who['mxid'],who['device'],time.time()))

def test_metadata_binds_room_device_txn_and_original_ciphertext(gateway):
    c,app,s=gateway;native(c,s)
    uploaded=c.post('/api/files/!direct',content=b'original-matrix-ciphertext');assert uploaded.status_code==200,uploaded.text
    fid=uploaded.json()['id'];path='/api/native/rooms/!direct/transactions/stable-sdk-txn'
    r=c.post(path,json={'attachments':[fid]});assert r.status_code==200,r.text
    assert c.post(path,json={'attachments':[fid]}).status_code==200
    assert c.post(path,json={'attachments':[]}).status_code==409
    seen=[]
    def handler(req):
        seen.append(json.loads(req.content));return httpx.Response(200,json={'event_id':'$original'})
    app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    opaque={'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'unchanged','session_id':'native'}
    sent=c.put('/_matrix/client/v3/rooms/!direct/send/m.room.encrypted/stable-sdk-txn',json=opaque)
    assert sent.status_code==200,sent.text
    assert seen[0]==opaque|{'org.yitiao.attachments':[fid]}
    status=c.get(path);assert status.status_code==200 and status.json()['event_id']=='$original'
    c.headers['Authorization']='Bearer bob-token'
    assert c.get(path).status_code==403

def test_metadata_rejects_body_keys_wrong_room_and_non_native(gateway):
    c,app,s=gateway;path='/api/native/rooms/!direct/transactions/txn'
    assert c.post(path,json={}).status_code==403
    native(c,s)
    for value in ({'body':'never upload decrypted text'},{'key':'never upload keys'},{'attachments':['not-a-file-id']},{'business':{'kind':'order','id':'not-real'}}):
        assert c.post(path,json=value).status_code in (400,403)
    fid=c.post('/api/files/!direct',content=b'cipher').json()['id']
    assert c.post('/api/native/rooms/!group/transactions/txn',json={'attachments':[fid]}).status_code==403

def test_native_admission_paces_business_and_rust_routes_together(gateway):
    c,app,s=gateway;native(c,s)
    app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
    start=time.monotonic()
    for path in ('/api/session','/_matrix/client/v3/keys/query','/api/wallet','/_matrix/client/v3/sync','/api/session','/api/wallet'):
        assert c.get(path).status_code==200
    assert time.monotonic()-start>=.95
    with connect(s.db) as db:
        who=db.execute("SELECT subject FROM sessions WHERE username='alice' LIMIT 1").fetchone()[0]
        db.execute("INSERT INTO rate VALUES(?,?,?) ON CONFLICT(subject,bucket) DO UPDATE SET next=excluded.next",(who,'native_requests',time.time()+.4))
    start=time.monotonic();assert c.get('/_matrix/client/v3/sync').status_code==200
    assert time.monotonic()-start>=.4

def test_native_yidt_return_is_fixed_and_cancel_still_invalidates(env):
    s,f,u=env
    o=f.create(u['alice'],{'room':'!test:chat.yitiao.org','kind':'direct'},{'key':'native-return-fixture-key','amount':'0.01'},'bob',native_return=True)
    with connect(s.ledger) as db:
        row=db.execute('SELECT * FROM yidt_intents WHERE id=(SELECT intent FROM yichat_orders WHERE id=?)',(o['id'],)).fetchone()
        assert row['return_url']==s.origin+'/app/return?order='+o['id'] and row['state']=='pending'
    assert f.cancel(u['alice'],o['id'])['state']=='cancelled'
    with connect(s.ledger) as db:assert db.execute('SELECT state FROM yidt_intents WHERE id=?',(row['id'],)).fetchone()[0]=='cancelled'

def test_local_uid_guard_429_also_cools_native_sdk(gateway):
    c,app,s=gateway;native(c,s)
    with connect(s.db) as db:
        uid=db.execute("SELECT subject FROM sessions WHERE username='alice' LIMIT 1").fetchone()[0]
        db.executemany('INSERT INTO request_hits(subject,at) VALUES(?,?)',[('user:'+uid,time.time())]*10)
    response=c.get('/_matrix/client/v3/sync');assert response.status_code==429
    with connect(s.db) as db:cooldown=db.execute("SELECT next FROM rate WHERE subject=? AND bucket='native_requests'",(uid,)).fetchone()[0]
    assert cooldown>=time.time()+.8
