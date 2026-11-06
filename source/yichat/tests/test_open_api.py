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


def test_stream_limit_is_three_per_account_and_delete_closes(gateway,monkeypatch):
    c,app,s=gateway
    monkeypatch.setattr(open_api,'RECHECK_SECONDS',0.2)
    one,two=new_key(c,'one'),new_key(c,'two')
    unthrottle(s)
    with api(app,one['key']).websocket_connect('/open/v1/stream') as a, \
         api(app,one['key'],'203.0.113.51').websocket_connect('/open/v1/stream') as b, \
         api(app,two['key'],'203.0.113.52').websocket_connect('/open/v1/stream?conversation=!direct') as d:
        for ws in (a,b,d):assert ws.receive_json()['type']=='ready'
        unthrottle(s)
        # A fourth concurrent subscription of the same account is refused, whatever the key.
        with api(app,two['key'],'203.0.113.53').websocket_connect('/open/v1/stream') as busy:
            assert busy.receive_json()['code']=='YICHAT_STREAM_LIMIT'
            with pytest.raises(WebSocketDisconnect) as e:busy.receive_json()
            assert e.value.code==4409
        # Another account is counted separately.
        bob=new_key(other(app,s,'bob-token'))['key'];unthrottle(s)
        with api(app,bob,'203.0.113.54').websocket_connect('/open/v1/stream') as theirs:
            assert theirs.receive_json()['type']=='ready'
        assert c.delete('/api/developer/keys/'+one['id']).status_code==200
        for ws in (a,b):
            msg=ws.receive_json()
            while msg['type']=='ping':msg=ws.receive_json()
            assert msg=={'type':'closing','code':'YICHAT_API_KEY_INVALID','error':'API Key 已删除或账户不可用'}
            with pytest.raises(WebSocketDisconnect) as e:ws.receive_json()
            assert e.value.code==4401
    # Slots are released when the connections end.
    unthrottle(s)
    with api(app,two['key']).websocket_connect('/open/v1/stream') as again:
        assert again.receive_json()['type']=='ready'


def test_stream_can_subscribe_to_one_conversation(gateway):
    c,app,s=gateway
    key=new_key(c)['key'];unthrottle(s);now=int(time.time()*1000)
    with api(app,key).websocket_connect('/open/v1/stream?conversation=!direct') as ws:
        ready=ws.receive_json();assert ready['type']=='ready' and ready['conversation_id']=='!direct'
        put(s,[text('$grp','!group',60,now,mxid(s,'bob'),'group only'),text('$dm','!direct',61,now,mxid(s,'bob'),'direct only')])
        msg=ws.receive_json()
        assert msg['conversation_id']=='!direct' and msg['message']['id']=='$dm'
    for target in ('!nope:chat.yitiao.org','bad id'):
        unthrottle(s)
        with api(app,key).websocket_connect('/open/v1/stream?conversation='+target) as ws:
            assert ws.receive_json()['code']=='YICHAT_NOT_MEMBER'
            with pytest.raises(WebSocketDisconnect) as e:ws.receive_json()
            assert e.value.code==4403


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


# ---------- text view of URL-shaped messages and attachments ----------

def order(s,app,oid,room,kind,amount,count=1,title='私聊转账'):
    units=str(app.state.funds.y.units(amount))
    with connect(s.ledger) as db:
        db.execute('''INSERT INTO yichat_orders(id,user,sid,room,kind,request_key,payload,source,title,principal,remaining,count,left_count,fees,state,intent,created)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(oid,'alice','sid-alice',room,kind,'key-'+oid,'[]','src',title,units,units,count,count,'0','awaiting_receipt','intent-'+oid,time.time()))


def event(eid,room,seq,at,sender,content):
    return (eid,room,seq,at,json.dumps({'type':'m.room.message','sender':sender,'origin_server_ts':at,'content':content}))


def test_url_messages_become_text_and_media_show_names(gateway):
    c,app,s=gateway;now=int(time.time()*1000)-60000;a=mxid(s,'alice')
    t1,t2,t3,t4='T'*32,'G'*32,'R'*32,'X'*32
    order(s,app,t1,'!direct','direct','0.01');order(s,app,t2,'!group','group','1',3,'恭喜发财')
    order(s,app,t4,'!group','group','5',1,'别的会话')
    url=lambda oid:'https://chat.yitiao.org/orders/'+oid
    origin_url=lambda oid:s.origin+'/orders/'+oid
    fid='ab'*24
    put(s,[
        event('$transfer','!direct',70,now,a,{'msgtype':'m.text','body':url(t1),'org.yitiao.share':{'type':'order','url':url(t1)},'org.yitiao.business':{'kind':'order','id':t1}}),
        event('$receipt','!direct',71,now+1,a,{'msgtype':'m.text','body':'已收款 · '+url(t1),'org.yitiao.share':{'type':'receipt','url':url(t1),'id':t1},'org.yitiao.business':{'kind':'order','id':t1}}),
        event('$foreign','!direct',72,now+2,a,{'msgtype':'m.text','body':url(t4)}),
        event('$image','!direct',73,now+3,a,{'msgtype':'m.image','body':'cat.png','info':{'mimetype':'image/png','size':12},'url':'mxc://chat.yitiao.org/'+fid}),
        event('$file','!direct',74,now+4,a,{'msgtype':'m.file','body':'report.pdf','info':{'mimetype':'application/pdf','size':3456},'url':'mxc://chat.yitiao.org/'+fid}),
        event('$gift','!direct',75,now+5,a,{'msgtype':'m.text','body':'https://chat.yitiao.org/gifts/'+'c'*32}),
        event('$tx','!direct',76,now+6,a,{'msgtype':'m.text','body':'看这笔 https://yidt.yitiao.org/explorer/tx/'+'d'*64+' 吧'}),
        event('$plain','!direct',77,now+7,a,{'msgtype':'m.text','body':'普通文字 https://example.com/x'}),
        event('$packet','!group',78,now+8,a,{'msgtype':'m.text','body':url(t2),'org.yitiao.business':{'kind':'order','id':t2}}),
    ])
    key=new_key(c)['key'];unthrottle(s)
    got={}
    for room in ('!direct','!group'):
        r=api(app,key).get('/open/v1/conversations/'+room+'/messages',params={'start':now-1000,'end':now+60000})
        assert r.status_code==200,r.text
        got.update({m['id']:m for m in r.json()['messages']})
    assert (got['$transfer']['kind'],got['$transfer']['text'])==('transfer','[转账] 0.01 YIDT')
    assert got['$transfer']['share']=={'type':'order','order_id':t1,'url':origin_url(t1),'amount':'0.01','currency':'YIDT','state':'awaiting_receipt'}
    assert (got['$receipt']['kind'],got['$receipt']['text'])==('receipt','[已收款] 0.01 YIDT')
    assert (got['$packet']['kind'],got['$packet']['text'])==('red_packet','[红包] 1 YIDT · 3 个 · 恭喜发财')
    # An order of another conversation is never described (no amount leak).
    assert got['$foreign']['text']=='[转账 / 红包]' and 'amount' not in got['$foreign']['share']
    assert (got['$image']['kind'],got['$image']['text'])==('image','[图片] cat.png')
    assert got['$image']['attachment']=={'id':fid,'name':'cat.png','mimetype':'image/png','size':12,'download_url':'/open/v1/files/'+fid}
    assert (got['$file']['kind'],got['$file']['text'],got['$file']['attachment']['size'])==('file','[文件] report.pdf',3456)
    assert (got['$gift']['kind'],got['$gift']['text'])==('gift','[赠卡]')
    assert got['$tx']['kind']=='share' and got['$tx']['text']=='看这笔 [分享] YIDT 交易 吧' and got['$tx']['share']['type']=='transaction'
    assert (got['$plain']['kind'],got['$plain']['text'],got['$plain']['share'])==('text','普通文字 https://example.com/x',None)


# ---------- sending (text, image, file) ----------

class FakeSynapse:
    """Stands in for Synapse: the send succeeds only if the module's grant check would pass."""
    def __init__(self,s):self.s=s;self.sent=[];self.deleted=[];self.reject=None;self.logins=0
    async def call(self,method,path,token=None,data=None,params=None):
        from fastapi import HTTPException
        from backend import api_grants
        if method=='PUT' and '/send/m.room.message/' in path:
            if self.reject:raise HTTPException(*self.reject)
            room=path.split('/rooms/')[1].split('/')[0].replace('%21','!')
            sender=mxid(self.s,'alice')
            assert api_grants.granted(self.s,room,sender,'m.room.message',data),'Synapse module would reject: no grant'
            assert token=='api-device-token'
            self.sent.append((room,path.rsplit('/',1)[1],data))
            return {'event_id':'$sent'+str(len(self.sent))}
        if method=='DELETE' and '/devices/' in path:self.deleted.append(path);return {}
        return {}
    async def login(self,mxid,password,device_id=None,device_name=''):
        self.logins+=1;self.device_name=device_name
        return {'access_token':'api-device-token','device_id':'APIDEVICE'}


def sending(gateway):
    c,app,s=gateway
    # New conversations are plain; the fixture DM uses the legacy 'direct' mode.
    with connect(s.db) as db:db.execute("UPDATE room_modes SET encryption='plain' WHERE room='!direct'")
    fake=FakeSynapse(s);m=app.state.matrix
    m.call=fake.call;m.login=fake.login;m.provision=AsyncMock()
    return c,app,s,fake


def test_send_text_uses_key_device_and_one_time_grant(gateway):
    c,app,s,fake=sending(gateway)
    created=new_key(c);key=created['key'];unthrottle(s)
    r=api(app,key).post('/open/v1/conversations/!direct/messages',json={'text':'你好','txn_id':'t-1'})
    assert r.status_code==200,r.text
    assert r.json()=={'event_id':'$sent1','conversation_id':'!direct','txn_id':'t-1'}
    assert fake.sent[0][1:]==('t-1',{'msgtype':'m.text','body':'你好'}) and fake.device_name=='YiChat API · bot'
    with connect(s.db) as db:
        row=db.execute('SELECT device,upstream FROM api_keys').fetchone()
        assert row['device']=='APIDEVICE' and 'api-device-token' not in row['upstream']
        assert db.execute('SELECT event FROM api_send_grants').fetchone()[0]=='$sent1'
    from backend import api_grants
    # Consumed: the same content is not admitted again without a new grant.
    assert not api_grants.granted(s,'!direct',mxid(s,'alice'),'m.room.message',{'msgtype':'m.text','body':'你好'})
    unthrottle(s)
    assert api(app,key).post('/open/v1/conversations/!group/messages',json={'text':'群里好'}).status_code==200
    assert fake.logins==1 and fake.sent[1][0]=='!group'
    # Deleting the key logs its Matrix device out and drops unused grants.
    assert c.delete('/api/developer/keys/'+created['id']).status_code==200
    assert fake.deleted and fake.deleted[0].endswith('/devices/APIDEVICE')


@pytest.mark.parametrize('body,headers,code',[
    ({'text':''},{},400),({'text':'x'*2001},{},400),({'text':'ok','extra':1},{},400),
    ({'text':'ok','txn_id':'bad txn!'},{},400),(None,{'Content-Type':'text/plain'},415),
])
def test_send_text_validation(gateway,body,headers,code):
    c,app,s,fake=sending(gateway)
    key=new_key(c)['key'];unthrottle(s)
    client=api(app,key)
    r=client.post('/open/v1/conversations/!direct/messages',content='ok',headers=headers) if body is None else client.post('/open/v1/conversations/!direct/messages',json=body)
    assert r.status_code==code,r.text
    assert not fake.sent


def test_send_requires_membership_and_plain_room(gateway):
    c,app,s,fake=sending(gateway)
    key=new_key(other(app,s,'bob-token'))['key'];unthrottle(s)
    r=api(app,key).post('/open/v1/conversations/!group/messages',json={'text':'not a member'})
    assert r.status_code==403 and r.json()['code']=='YICHAT_NOT_MEMBER'
    with connect(s.db) as db:db.execute("UPDATE room_modes SET encryption='legacy_encrypted' WHERE room='!direct'")
    unthrottle(s)
    r=api(app,key).post('/open/v1/conversations/!direct/messages',json={'text':'old room'})
    assert r.status_code==403 and r.json()['code']=='YICHAT_LEGACY_ROOM'
    assert not fake.sent


def test_send_rejection_is_reported_and_dead_device_is_reset(gateway):
    c,app,s,fake=sending(gateway)
    key=new_key(c)['key'];unthrottle(s)
    fake.reject=(403,{'errcode':'M_FORBIDDEN','error':'对方已拒绝私聊'})
    r=api(app,key).post('/open/v1/conversations/!direct/messages',json={'text':'hi'})
    assert r.status_code==403 and r.json()=={'errcode':'M_FORBIDDEN','code':'YICHAT_SEND_REJECTED','error':'对方已拒绝私聊'}
    fake.reject=(401,{'errcode':'M_UNKNOWN_TOKEN','error':'Invalid access token'});unthrottle(s)
    r=api(app,key).post('/open/v1/conversations/!direct/messages',json={'text':'hi'})
    assert r.status_code==503 and r.json()['code']=='YICHAT_API_DEVICE_RESET'
    with connect(s.db) as db:assert db.execute('SELECT upstream FROM api_keys').fetchone()[0] is None
    fake.reject=None;unthrottle(s)
    assert api(app,key).post('/open/v1/conversations/!direct/messages',json={'text':'hi'}).status_code==200
    assert fake.logins==2


PNG=b'\x89PNG\r\n\x1a\n'+b'\x00'*32


def test_send_image_and_file_then_download_once_per_request(gateway):
    c,app,s,fake=sending(gateway)
    key=new_key(c)['key'];unthrottle(s)
    r=api(app,key).post('/open/v1/conversations/!direct/files',params={'name':'cat.png'},content=PNG,headers={'Content-Type':'image/png'})
    assert r.status_code==200,r.text
    image=r.json();fid=image['attachment']['id']
    assert image['kind']=='image' and image['attachment']['size']==len(PNG) and image['attachment']['download_url']=='/open/v1/files/'+fid
    sent=fake.sent[0][2]
    assert sent['msgtype']=='m.image' and sent['body']=='cat.png' and sent['url']=='mxc://chat.yitiao.org/'+fid
    assert sent['org.yitiao.attachments']==[fid] and sent['info']=={'mimetype':'image/png','size':len(PNG)}
    unthrottle(s)
    r=api(app,key).post('/open/v1/conversations/!direct/files',params={'name':'pic.png','type':'file'},content=PNG,headers={'Content-Type':'image/png'})
    assert r.status_code==200 and r.json()['kind']=='file' and fake.sent[1][2]['msgtype']=='m.file'
    unthrottle(s)
    r=api(app,key).post('/open/v1/conversations/!direct/files',params={'name':'notes.txt'},content=b'hello',headers={'Content-Type':'text/plain'})
    assert r.status_code==200 and fake.sent[2][2]['msgtype']=='m.file'
    unthrottle(s)
    got=api(app,key).get('/open/v1/files/'+fid)
    assert got.status_code==200 and got.content==PNG and got.headers['content-disposition']=='attachment'
    unthrottle(s)
    assert api(app,key).get('/open/v1/files/'+'0'*48).status_code==404
    assert api(app,key).get('/open/v1/files/not-an-id').status_code==404


@pytest.mark.parametrize('params,content,headers,code',[
    ({},PNG,{'Content-Type':'image/png'},400),
    ({'name':'a/b.png'},PNG,{'Content-Type':'image/png'},400),
    ({'name':'x.txt','type':'image'},b'hi',{'Content-Type':'text/plain'},415),
    ({'name':'x','type':'video'},b'hi',{'Content-Type':'text/plain'},400),
    ({'name':'empty.txt'},b'',{'Content-Type':'text/plain'},400),
    ({'name':'x.bin'},b'hi',{'Content-Type':'not a type'},400),
])
def test_send_file_validation(gateway,params,content,headers,code):
    c,app,s,fake=sending(gateway)
    key=new_key(c)['key'];unthrottle(s)
    r=api(app,key).post('/open/v1/conversations/!direct/files',params=params,content=content,headers=headers)
    assert r.status_code==code,r.text
    assert not fake.sent


def test_upload_download_and_send_share_the_account_limit(gateway):
    c,app,s,fake=sending(gateway)
    key=new_key(c)['key'];unthrottle(s)
    client=api(app,key)
    fid=client.post('/open/v1/conversations/!direct/files',params={'name':'a.txt'},content=b'a',headers={'Content-Type':'text/plain'}).json()['attachment']['id']
    assert client.post('/open/v1/conversations/!direct/messages',json={'text':'1'}).status_code==200
    assert client.get('/open/v1/files/'+fid).status_code==200
    assert client.get('/open/v1/conversations').status_code==200
    assert client.get('/open/v1/files/'+fid).status_code==200
    r=client.post('/open/v1/conversations/!direct/files',params={'name':'b.txt'},content=b'b',headers={'Content-Type':'text/plain'})
    assert r.status_code==429 and r.json()['code']=='YICHAT_RATE_LIMITED'


def test_grant_requires_live_key_and_expires(gateway):
    c,app,s=gateway
    from backend import api_grants
    created=new_key(c);a=mxid(s,'alice');content={'msgtype':'m.text','body':'x'}
    with connect(s.db) as db:kid=db.execute('SELECT id FROM api_keys').fetchone()[0]
    api_grants.create(s,kid,'!direct',a,'m.room.message',content)
    assert api_grants.granted(s,'!direct',a,'m.room.message',content)
    assert not api_grants.granted(s,'!direct',a,'m.room.message',{'msgtype':'m.text','body':'y'})
    assert not api_grants.granted(s,'!group',a,'m.room.message',content)
    assert not api_grants.granted(s,'!direct',mxid(s,'bob'),'m.room.message',content)
    with connect(s.db) as db:db.execute('UPDATE api_send_grants SET expires=0')
    assert not api_grants.granted(s,'!direct',a,'m.room.message',content)
    api_grants.create(s,kid,'!direct',a,'m.room.message',content)
    assert c.delete('/api/developer/keys/'+created['id']).status_code==200
    assert not api_grants.granted(s,'!direct',a,'m.room.message',content)
