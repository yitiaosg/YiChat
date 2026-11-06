"""Developer API: web-only key management, REST reads and the WebSocket stream.
Isolated fixture data only (yc_test_* PostgreSQL); no real accounts or Synapse."""
import json,time
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from backend import archive,history,open_api
from backend.db import connect
from test_gateway import gateway

DAY=86400000


def put(s,rows):
    archive.ingest_batch(s.archive_ingest,rows,max(r[2] for r in rows),int(time.time()*1000))
    history.record_rows(s,rows)


def text(eid,room,seq,at,sender,body):
    return (eid,room,seq,at,json.dumps({'type':'m.room.message','sender':sender,'origin_server_ts':at,'content':{'msgtype':'m.text','body':body}}))


def unthrottle(s):
    with connect(s.db) as db:db.execute('DELETE FROM request_hits')


def new_key(c,name='bot'):
    r=c.post('/api/developer/keys',json={'name':name});assert r.status_code==200,r.text
    return r.json()


def other(app,s,cookie,ip='198.51.100.7'):
    c=TestClient(app,client=(ip,1234));c.cookies.set('yc_session',cookie);c.headers['Origin']=s.origin
    return c


def api(app,key,ip='203.0.113.1'):
    c=TestClient(app,client=(ip,4321));c.headers['Authorization']='Bearer '+key
    return c


def mxid(s,name):
    with connect(s.db) as db:return db.execute('SELECT mxid FROM identities WHERE username=?',(name,)).fetchone()[0]


# ---------- key management (Web version only) ----------

def test_key_is_shown_once_listed_without_secret_and_limited_to_three(gateway):
    c,app,s=gateway
    created=new_key(c,'first')
    assert open_api.KEY.fullmatch(created['key']) and created['prefix']==created['key'][:12]
    listing=c.get('/api/developer/keys').json()
    assert listing['limit']==3 and listing['docs_url'].startswith('https://github.com/')
    assert [k['id'] for k in listing['keys']]==[created['id']] and 'key' not in listing['keys'][0]
    with connect(s.db) as db:
        stored=db.execute('SELECT * FROM api_keys').fetchone()
        assert created['key'] not in json.dumps(dict(stored)) and stored['key_hash']==open_api.digest(created['key'])
    new_key(c,'second');new_key(c,'third')
    r=c.post('/api/developer/keys',json={'name':'fourth'})
    assert r.status_code==409 and r.json()['code']=='YICHAT_API_KEY_LIMIT'
    assert c.delete('/api/developer/keys/'+created['id']).status_code==200
    assert new_key(c,'again')['id']


@pytest.mark.parametrize('name',['',' ','x'*41,'a\nb',None,7])
def test_key_name_validation(gateway,name):
    c,app,s=gateway
    assert c.post('/api/developer/keys',json={'name':name}).status_code==400


def test_key_management_rejects_app_style_and_cross_site_requests(gateway):
    c,app,s=gateway
    # Bearer-only (how Android/desktop authenticate): no browser cookie.
    app_style=TestClient(app);app_style.headers['Authorization']='Bearer session-token'
    for r in (app_style.get('/api/developer/keys'),app_style.post('/api/developer/keys',json={'name':'x'})):
        assert r.status_code==403 and r.json()['code']=='YICHAT_WEB_ONLY'
    # Cookie plus a different Bearer session.
    assert c.get('/api/developer/keys',headers={'Authorization':'Bearer device-two'}).status_code==403
    assert c.get('/api/developer/keys',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert c.post('/api/developer/keys',json={'name':'x'},headers={'Origin':'https://evil.example'}).status_code==403
    # A session that belongs to a native App device.
    with connect(s.db) as db:
        who=db.execute("SELECT * FROM sessions WHERE username='alice' LIMIT 1").fetchone()
        db.execute('INSERT INTO native_devices(proof_hash,subject,mxid,device,created,platform) VALUES(?,?,?,?,?,?)',('p',who['subject'],who['mxid'],who['device'],time.time(),'windows'))
    assert c.get('/api/developer/keys').status_code==403


def test_keys_are_per_account_and_only_work_on_open_api(gateway):
    c,app,s=gateway;app.state.matrix.call=AsyncMock(return_value={})
    mine=new_key(c)
    bob=other(app,s,'bob-token')
    assert bob.get('/api/developer/keys').json()['keys']==[]
    assert bob.delete('/api/developer/keys/'+mine['id']).status_code==404
    plain=TestClient(app)
    assert plain.get('/api/session',headers={'Authorization':'Bearer '+mine['key']}).status_code==401
    assert plain.get('/open/v1/conversations',headers={'Authorization':'Bearer session-token'}).status_code==401
    assert plain.get('/open/v1/conversations').status_code==401
    assert c.delete('/api/developer/keys/'+mine['id']).status_code==200
    unthrottle(s)
    assert api(app,mine['key']).get('/open/v1/conversations').status_code==401


# ---------- rate limits ----------

def test_account_limit_is_shared_across_keys_and_ips(gateway):
    c,app,s=gateway;app.state.matrix.call=AsyncMock(return_value={})
    one,two=new_key(c,'one')['key'],new_key(c,'two')['key'];unthrottle(s)
    for i,(key,ip) in enumerate([(one,'203.0.113.1'),(two,'203.0.113.2'),(one,'203.0.113.3'),(two,'203.0.113.4'),(one,'203.0.113.5')]):
        assert api(app,key,ip).get('/open/v1/conversations').status_code==200,i
    r=api(app,two,'203.0.113.6').get('/open/v1/conversations')
    assert r.status_code==429 and r.json()['code']=='YICHAT_RATE_LIMITED' and 'Retry-After' in r.headers


def test_no_ip_limit_accounts_on_one_ip_are_independent(gateway):
    c,app,s=gateway;app.state.matrix.call=AsyncMock(return_value={})
    alice=new_key(c)['key'];bob=new_key(other(app,s,'bob-token'))['key'];unthrottle(s)
    # Ten requests from one IP: five per account, so neither account is limited.
    for key in (alice,bob)*5:assert api(app,key,'203.0.113.9').get('/open/v1/conversations').status_code==200
    assert api(app,bob,'203.0.113.9').get('/open/v1/conversations').status_code==429
    # Invalid keys from one IP are rejected as 401, never rate limited by IP.
    unthrottle(s)
    codes=[api(app,'yck_'+'x'*43,'203.0.113.11').get('/open/v1/conversations').status_code for _ in range(8)]
    assert codes==[401]*8
    with connect(s.db) as db:assert db.execute("SELECT count(*) FROM request_hits WHERE subject LIKE 'api-ip:%'").fetchone()[0]==0


# ---------- REST reads ----------

def test_conversations_include_archived_with_names(gateway):
    c,app,s=gateway
    app.state.matrix.call=AsyncMock(return_value={'name':'测试群'})
    key=new_key(c)['key'];now=int(time.time()*1000)
    put(s,[text('$m1','!direct',10,now-1000,mxid(s,'bob'),'hi')])
    with connect(s.db) as db:db.execute('INSERT INTO conversation_preferences(subject,room,archived) VALUES((SELECT subject FROM identities WHERE username=?),?,1)',('alice','!group'))
    unthrottle(s)
    rows={r['id']:r for r in api(app,key).get('/open/v1/conversations').json()['conversations']}
    assert set(rows)=={'!direct','!group'}
    assert rows['!direct']['type']=='direct' and rows['!direct']['peer']['username']=='bob' and rows['!direct']['name']=='bob'
    assert rows['!direct']['last_message_at']==now-1000 and not rows['!direct']['archived']
    assert rows['!group']['name']=='测试群' and rows['!group']['archived'] is True
    unthrottle(s)
    assert [r['id'] for r in api(app,key).get('/open/v1/conversations?archived=only').json()['conversations']]==['!group']
    assert [r['id'] for r in api(app,key).get('/open/v1/conversations?archived=exclude').json()['conversations']]==['!direct']
    assert api(app,key).get('/open/v1/conversations?archived=all').status_code==400


def test_messages_window_membership_redaction_and_paging(gateway):
    c,app,s=gateway
    key=new_key(c)['key'];now=int(time.time()*1000);a,b=mxid(s,'alice'),mxid(s,'bob')
    enc=('$enc','!direct',14,now-3000,json.dumps({'type':'m.room.encrypted','sender':b,'origin_server_ts':now-3000,'content':{'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'opaque'}}))
    red=('$red','!direct',16,now-500,json.dumps({'type':'m.room.redaction','sender':a,'redacts':'$m2','origin_server_ts':now-500,'content':{'redacts':'$m2'}}))
    put(s,[text('$m1','!direct',11,now-5000,b,'one'),text('$m2','!direct',12,now-4000,a,'two'),enc,text('$m3','!direct',15,now-2000,b,'three'),red])
    unthrottle(s)
    r=api(app,key).get('/open/v1/conversations/!direct/messages',params={'start':now-DAY+10,'end':now})
    assert r.status_code==200,r.text
    got=r.json()['messages']
    assert [m['id'] for m in got]==['$m1','$m2','$enc','$m3'] and r.json()['next_after'] is None
    assert got[0]['text']=='one' and got[0]['sender']=={'mxid':b,'username':'bob'} and got[0]['msgtype']=='m.text'
    assert got[1]['redacted'] and got[1]['text'] is None and got[1]['content'] is None
    assert got[2]['encrypted'] and got[2]['content'] is None
    unthrottle(s)
    page=api(app,key).get('/open/v1/conversations/!direct/messages',params={'start':now-DAY+10,'end':now,'limit':2}).json()
    assert [m['id'] for m in page['messages']]==['$m1','$m2'] and page['next_after']=='12'
    rest=api(app,key).get('/open/v1/conversations/!direct/messages',params={'start':now-DAY+10,'end':now,'limit':2,'after':page['next_after']}).json()
    assert [m['id'] for m in rest['messages']]==['$enc','$m3']


def test_messages_before_join_are_hidden(gateway):
    c,app,s=gateway
    key=new_key(c)['key'];now=int(time.time()*1000)
    # The fixture's join events were received at now-1 day.
    put(s,[text('$old','!direct',20,now-DAY-60000,mxid(s,'bob'),'before join'),text('$new','!direct',21,now-100000,mxid(s,'bob'),'after join')])
    unthrottle(s)
    start=now-DAY-90000
    r=api(app,key).get('/open/v1/conversations/!direct/messages',params={'start':start,'end':start+DAY})
    assert [m['id'] for m in r.json()['messages']]==['$new']


@pytest.mark.parametrize('params,code',[
    ({'start':0,'end':DAY+1},400),({'start':10,'end':10},400),({'start':-1,'end':5},400),
    ({'start':0,'end':10,'limit':0},400),({'start':0,'end':10,'limit':201},400),({'start':0,'end':10,'after':'x'},400),
    ({'end':10},422)])
def test_message_query_bounds(gateway,params,code):
    c,app,s=gateway
    key=new_key(c)['key'];unthrottle(s)
    r=api(app,key).get('/open/v1/conversations/!direct/messages',params=params)
    assert r.status_code==code,r.text
    if params.get('end')==DAY+1:assert r.json()['code']=='YICHAT_RANGE_TOO_LONG'


def test_messages_require_current_membership(gateway):
    c,app,s=gateway
    key=new_key(other(app,s,'bob-token'))['key'];unthrottle(s)
    r=api(app,key).get('/open/v1/conversations/!group/messages',params={'start':0,'end':1000})
    assert r.status_code==403 and r.json()['code']=='YICHAT_NOT_MEMBER'


# ---------- WebSocket stream ----------

def test_stream_delivers_new_messages_and_redactions(gateway,monkeypatch):
    c,app,s=gateway
    key=new_key(c)['key'];unthrottle(s);now=int(time.time()*1000)
    with api(app,key).websocket_connect('/open/v1/stream') as ws:
        ready=ws.receive_json();assert ready['type']=='ready' and ready['ping_interval']<=60
        put(s,[text('$live','!direct',30,now,mxid(s,'bob'),'live one'),text('$other','!group',31,now,mxid(s,'alice'),'group')])
        first=ws.receive_json();second=ws.receive_json()
        assert {first['message']['id'],second['message']['id']}=={'$live','$other'}
        live=first if first['message']['id']=='$live' else second
        assert live['type']=='message' and live['conversation_id']=='!direct' and live['message']['text']=='live one'
        put(s,[('$r','!direct',32,now+1,json.dumps({'type':'m.room.redaction','sender':mxid(s,'bob'),'redacts':'$live','content':{'redacts':'$live'}}))])
        assert ws.receive_json()=={'type':'redaction','conversation_id':'!direct','message_id':'$live','sequence':'32'}


def test_stream_only_sends_rooms_of_the_account(gateway):
    c,app,s=gateway
    key=new_key(other(app,s,'bob-token'))['key'];unthrottle(s);now=int(time.time()*1000)
    with api(app,key).websocket_connect('/open/v1/stream') as ws:
        assert ws.receive_json()['type']=='ready'
        put(s,[text('$g','!group',40,now,mxid(s,'alice'),'not for bob'),text('$d','!direct',41,now,mxid(s,'alice'),'for bob')])
        assert ws.receive_json()['message']['id']=='$d'


def test_stream_pings_when_idle_and_answers_client_ping(gateway,monkeypatch):
    c,app,s=gateway
    monkeypatch.setattr(open_api,'PING_SECONDS',0.3)
    key=new_key(c)['key'];unthrottle(s)
    with api(app,key).websocket_connect('/open/v1/stream') as ws:
        assert ws.receive_json()['type']=='ready'
        assert ws.receive_json()['type']=='ping'
        ws.send_json({'type':'ping'})
        seen=[ws.receive_json()['type'] for _ in range(2)]
        assert 'pong' in seen


def test_stream_one_connection_per_key_and_closes_after_delete(gateway,monkeypatch):
    c,app,s=gateway
    monkeypatch.setattr(open_api,'RECHECK_SECONDS',0.2)
    created=new_key(c);unthrottle(s)
    with api(app,created['key']).websocket_connect('/open/v1/stream') as ws:
        assert ws.receive_json()['type']=='ready'
        with api(app,created['key'],'203.0.113.50').websocket_connect('/open/v1/stream') as busy:
            assert busy.receive_json()['code']=='YICHAT_STREAM_BUSY'
            with pytest.raises(WebSocketDisconnect) as e:busy.receive_json()
            assert e.value.code==4409
        assert c.delete('/api/developer/keys/'+created['id']).status_code==200
        msg=ws.receive_json()
        while msg['type']=='ping':msg=ws.receive_json()
        assert msg=={'type':'closing','code':'YICHAT_API_KEY_INVALID','error':'API Key 已删除或账户不可用'}
        with pytest.raises(WebSocketDisconnect) as e:ws.receive_json()
        assert e.value.code==4401


def test_stream_rejects_missing_or_invalid_key(gateway):
    c,app,s=gateway
    for headers in ({},{'Authorization':'Bearer session-token'},{'Authorization':'Bearer yck_'+'a'*43}):
        unthrottle(s)
        with TestClient(app).websocket_connect('/open/v1/stream',headers=headers) as ws:
            assert ws.receive_json()['code']=='YICHAT_API_KEY_INVALID'
            with pytest.raises(WebSocketDisconnect) as e:ws.receive_json()
            assert e.value.code==4401


def test_stream_handshake_account_limit_closes_with_4429(gateway):
    c,app,s=gateway;app.state.matrix.call=AsyncMock(return_value={})
    key=new_key(c)['key'];unthrottle(s)
    # Five REST calls from other IPs use up the account's budget for this second.
    for i in range(5):assert api(app,key,'203.0.113.'+str(70+i)).get('/open/v1/conversations').status_code==200
    with api(app,key,'203.0.113.77').websocket_connect('/open/v1/stream') as ws:
        assert ws.receive_json()['code']=='YICHAT_RATE_LIMITED'
        with pytest.raises(WebSocketDisconnect) as e:ws.receive_json()
        assert e.value.code==4429
