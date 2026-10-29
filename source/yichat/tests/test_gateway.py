import time,json
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import connect
from backend.security import digest
from backend import archive,policy
import account_social as social

@pytest.fixture
def gateway(env):
    s,f,u=env;s.funds_enabled=False
    app=create_app(s)
    with TestClient(app) as client:
        cipher=app.state.cipher
        with connect(s.db) as c:
            for user,token in [('alice','session-token'),('alice','device-two'),('bob','bob-token')]:
                c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),u[user]['subject'],user,'sid-'+user,u[user]['mxid'],'device',cipher.encrypt(b'upstream').decode(),time.time()+3600))
            for rid,kind,peer,mode in [('!direct','direct',u['bob']['mxid'],'direct'),('!group','group',None,'plain')]:
                c.execute('INSERT INTO rooms VALUES(?,?,?,?,30)',(rid,kind,u['alice']['mxid'],peer))
                c.execute('INSERT INTO room_modes VALUES(?,?,?)',(rid,mode,int(time.time()*1000)))
        # Historical visibility evidence comes from the real ingestion adapter in
        # integration tests; this API fixture inserts its accepted source rows.
        now=int(time.time()*1000)
        rows=[]
        for i,(room,name) in enumerate([('!direct','alice'),('!direct','bob'),('!group','alice')],1):
            rows.append(('$join'+str(i),room,i,now-86400000,json.dumps({'type':'m.room.member','state_key':u[name]['mxid'],'sender':u[name]['mxid'],'content':{'membership':'join'}})))
        archive.ingest_batch(s.archive_ingest,rows,3,now)
        # Current membership is a separate permission barrier from archive history.
        # The mocked Matrix adapter must seed both, as real Synapse ingestion does.
        with connect(s.ledger) as ledger:
            for event,room,_,_,raw in rows:
                ledger.execute("INSERT INTO yichat_memberships(room,mxid,membership,event_id) VALUES(?,?,'join',?)",(room,json.loads(raw)['state_key'],event))
        app.state.matrix.membership=AsyncMock();app.state.matrix.member=AsyncMock();app.state.matrix.fence=AsyncMock(return_value=3)
        client.cookies.set('yc_session','session-token');client.headers['Origin']=s.origin
        yield client,app,s


def test_native_unmanaged_entrypoints_and_plaintext_are_denied(gateway):
    c,app,s=gateway
    for path in ('/_matrix/media/v3/upload','/_matrix/client/v1/media/upload','/_matrix/client/v3/register','/_matrix/client/v3/login','/_matrix/client/v3/createRoom','/_matrix/federation/v1/send/test','/_matrix/client/v3/rooms/!direct/send/m.room.message/id'):
        r=c.post(path,json={});assert r.status_code in (403,404,405),(path,r.text)


def test_actual_file_receipt_hash_and_uniform_30_day_boundary(gateway):
    c,app,s=gateway
    before=time.time();r=c.post('/api/files/!direct',content=b'ciphertext');assert r.status_code==200,r.text
    file=r.json();assert file['expires']==(int(before//86400)+31)*86400
    assert c.get('/api/files/'+file['id']+'/content').content==b'ciphertext'
    with archive.connect(s.archive_reader) as pg:
        receipt=pg.execute('SELECT * FROM attachments WHERE id=%s',(file['id'],)).fetchone()
        assert receipt['sha256']==archive.sha256(b'ciphertext') and receipt['bytes']==10
    assert c.put('/api/rooms/!group/retention',json={'days':7}).status_code==404
    with connect(s.db) as db:db.execute('UPDATE files SET expires=? WHERE id=?',(time.time()-1,file['id']))
    assert c.get('/api/files/'+file['id']+'/content').status_code==410


def test_draft_attachment_is_owner_only_until_accepted_reference(gateway):
    c,app,s=gateway
    result=c.post('/api/files/!direct',content=b'opaque ciphertext').json();fid=result['id']
    c.cookies.clear();c.headers['Authorization']='Bearer bob-token'
    assert c.get('/api/files/'+fid+'/content').status_code==403
    now=int(time.time()*1000)
    declaration={'type':'m.room.encrypted','sender':'@alice:chat.yitiao.org','content':{'ciphertext':'opaque','org.yitiao.attachments':[fid]}}
    policy.admit_attachments(s,'$reference','!direct','@alice:chat.yitiao.org','m.room.encrypted',declaration['content'],'writer','boot')
    archive.ingest_batch(s.archive_ingest,[('$reference','!direct',4,now,json.dumps(declaration))],4,now)
    policy.finish_attachments(s,'$reference',now)
    app.state.matrix.fence.return_value=4
    assert c.get('/api/files/'+fid+'/content').content==b'opaque ciphertext'
    with connect(s.db) as local:assert not local.execute('SELECT * FROM attachment_admissions').fetchall()


def test_privacy_live_applies_to_attachments_funds_and_native_send(gateway):
    c,app,s=gateway;a=policy.identity(s,'alice')['uid'];b=policy.identity(s,'bob')['uid']
    social.mutate(s.accounts,b,a,'block')
    for path,data in [('/api/files/!direct',None),('/api/orders',{'room':'!direct'}),('/_matrix/client/v3/rooms/!direct/send/m.room.encrypted/txn',{'ciphertext':'x'})]:
        r=c.post(path,content=b'x') if data is None else c.put(path,json=data) if '/send/' in path else c.post(path,json=data)
        assert r.status_code==403,(path,r.text)
        assert r.json()['code']=='YICHAT_MESSAGE_FORBIDDEN'
        assert r.json()['error']==social.PRIVACY_MESSAGE
    # Sharing a group is allowed despite a private block.
    r=c.post('/api/files/!group',content=b'plain group file');assert r.status_code==200,r.text


def test_main_logout_immediately_revokes_all_gateway_devices(gateway):
    c,app,s=gateway
    assert c.get('/api/session').status_code==200
    with connect(s.ledger) as db:db.execute("UPDATE yidt_sessions SET revoked=1 WHERE user='alice'")
    for token in ('session-token','device-two'):
        c.cookies.clear();c.headers['Authorization']='Bearer '+token
        for path in ('/api/session','/api/wallet','/_matrix/client/v3/sync'):
            assert c.get(path).status_code==401
    # A revoked cookie must not trap a user outside the OAuth login flow.
    c.headers.pop('Authorization');c.cookies.set('yc_session','session-token')
    assert c.get('/auth/login',follow_redirects=False).status_code==307


def test_session_reports_uniform_gmt_zero_retention(gateway):
    c,app,s=gateway
    assert c.get('/api/session').json()['retention']=={'days':30,'timezone':'GMT+0','basis':'utc_day_end','cleanup_requires_sealed_archive':True}
    for rid in ['!group','!direct']:
        assert c.get('/api/rooms/'+rid).json()['days']==30


def test_shared_ten_per_second_across_devices_and_business_matrix_routes(gateway):
    c,app,s=gateway
    for i in range(10):
        c.cookies.clear();c.headers['Authorization']='Bearer '+('session-token' if i%2 else 'device-two')
        r=c.get('/api/session' if i%2 else '/api/wallet');assert r.status_code==200,r.text
    r=c.get('/_matrix/client/versions');assert r.status_code==429
    assert r.headers['Retry-After']=='1' and r.json()['errcode']=='M_LIMIT_EXCEEDED'
    assert 0<r.json()['retry_after_ms']<=1000
    c.headers['Authorization']='Bearer bob-token';assert c.get('/api/session').status_code==200
    time.sleep(1.02);c.headers['Authorization']='Bearer session-token';assert c.get('/api/session').status_code==200


def test_upload_origin_actual_body_limit_chunked_and_abort_cleanup(gateway):
    c,app,s=gateway
    assert c.post('/api/files/!direct',content=b'hello',headers={'Origin':'https://evil.example'}).status_code==403
    s.max_file=3
    assert c.post('/api/files/!direct',content=b'hello').status_code==413
    r=c.post('/api/files/!direct',content=iter([b'ab',b'cd']));assert r.status_code==413,r.text
    assert not list(s.objects.glob('*.part'))
    with connect(s.db) as db:assert db.execute('SELECT count(*) FROM upload_leases').fetchone()[0]==0


def test_native_media_uses_same_context_policy_rate_and_receipt(gateway):
    c,app,s=gateway
    r=c.post('/_matrix/media/v3/upload?room_id=!direct',content=b'encrypted-native')
    assert r.status_code==200,r.text
    fid=r.json()['id'];assert r.json()['content_uri']=='mxc://chat.yitiao.org/'+fid
    assert c.get('/_matrix/client/v1/media/download/chat.yitiao.org/'+fid).content==b'encrypted-native'
    app.state.matrix.fence.return_value=1000
    assert c.get('/api/files/'+fid+'/content').status_code==503


def test_body_without_content_length_is_bounded(gateway):
    c,app,s=gateway
    r=c.post('/api/preview',content=iter([b'x'*65536]*5),headers={'Content-Type':'application/json'})
    assert r.status_code==413,r.text


def test_two_connections_share_actual_stream_byte_rate(gateway):
    import concurrent.futures
    c,app,s=gateway
    started=time.monotonic()
    def upload(_):return c.post('/api/files/!direct',content=b'x'*500000).status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(upload,range(2)))==[200,200]
    assert time.monotonic()-started>=1.95


def test_plain_messages_limit_and_e2ee_disabled(gateway):
    c,app,s=gateway
    from fastapi import HTTPException
    info=policy.room(s,'!group')
    policy.validate_event(s,info,'m.room.message',{'body':'😀'*2000},'@alice:chat.yitiao.org')
    with pytest.raises(HTTPException):policy.validate_event(s,info,'m.room.message',{'body':'😀'*2001},'@alice:chat.yitiao.org')
    with pytest.raises(HTTPException):policy.validate_event(s,info,'m.room.message',{'body':'ok','m.new_content':{'body':'x'*2001}},'@alice:chat.yitiao.org')
    for room in ('!direct','!group'):
        info=policy.room(s,room)
        with pytest.raises(HTTPException) as exc:policy.validate_event(s,info,'m.room.encrypted',{'ciphertext':'opaque'},'@alice:chat.yitiao.org')
        assert exc.value.detail['code']=='YICHAT_E2EE_DISABLED'
        with pytest.raises(HTTPException):policy.validate_event(s,info,'m.room.encryption',{'algorithm':'m.megolm.v1.aes-sha2'},'@alice:chat.yitiao.org','')

def test_preview_no_fetch_no_finance_effect_and_route_allowlist(gateway):
    c,app,s=gateway
    for url in ('https://127.0.0.1/','https://yitiao.org.evil.test/','https://yidt.yitiao.org/explorer/tx/'+'a'*64):
        assert c.post('/api/preview',json={'url':url}).json() is None
    with connect(s.ledger) as db:assert db.execute('SELECT count(*) FROM yichat_orders').fetchone()[0]==0


def test_profile_canonical_url_and_unknown_avatar(gateway):
    c,app,s=gateway
    directory=s.site/'static/avatar';directory.mkdir(parents=True);(directory/'alice.jpg').write_bytes(b'test-avatar')
    out=c.post('/api/profiles',json={'mxids':['@alice:chat.yitiao.org']}).json()
    assert out[0]['profile_url']=='https://yitiao.org/@alice/'
    assert c.get('/api/avatars/alice').content==b'test-avatar'
    assert c.get('/api/avatars/..%2F..%2Fuserdata.db').status_code==404


def test_partial_historical_attachment_access_denied(gateway):
    c,app,s=gateway
    result=c.post('/api/files/!direct',content=b'secret cipher').json()
    with archive.connect(s.archive_ingest) as pg:
        pg.execute('INSERT INTO memberships VALUES(%s,%s,%s,%s,%s,%s)',('$late','!direct','@eve:chat.yitiao.org',100,int(time.time()*1000)+1000,'join'))
    with connect(s.db) as db:
        uid=policy.identity(s,'eve')['uid']
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest('eve-token'),uid,'eve','sid-eve','@eve:chat.yitiao.org','d',app.state.cipher.encrypt(b'upstream').decode(),time.time()+300))
    c.cookies.clear();c.headers['Authorization']='Bearer eve-token'
    assert c.get('/api/files/'+result['id']+'/content').status_code==403


def test_authenticated_archive_downloads_identical_partial_denied_and_summary_survives(gateway,postgres):
    from datetime import date
    c,app,s=gateway;day=date(2026,1,2);start,end=archive.day_bounds(day)
    now=int(time.time()*1000)
    rows=[]
    for seq,name,at in [(10,'alice',start-1),(11,'bob',start-1),(12,'eve',start+1000)]:
        rows.append(('$hist-'+name,'!group',seq,at,json.dumps({'type':'m.room.member','state_key':'@'+name+':chat.yitiao.org','sender':'@'+name+':chat.yitiao.org','content':{'membership':'join'}})))
    rows.append(('$msg','!group',13,start+2000,json.dumps({'type':'m.room.message','sender':'@alice:chat.yitiao.org','content':{'body':'accepted group event'}})))
    archive.ingest_batch(s.archive_ingest,rows,13,now,[(day,13,now)])
    trusted=archive.seal_day(s,'!group',day,'events-manifest',now)
    url='/api/archives/!group/2026-01-02/'
    a=c.get(url+'download');assert a.status_code==200,a.text
    c.cookies.clear();c.headers['Authorization']='Bearer bob-token'
    b=c.get(url+'download');assert b.status_code==200 and b.content==a.content
    assert archive.sha256(b.content)==trusted['sha256']
    with connect(s.db) as db:
        uid=policy.identity(s,'eve')['uid']
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest('partial'),uid,'eve','sid-eve','@eve:chat.yitiao.org','d',app.state.cipher.encrypt(b'upstream').decode(),time.time()+300))
    c.headers['Authorization']='Bearer partial';assert c.get(url+'summary').status_code==403
    c.headers['Authorization']='Bearer bob-token'
    archive.artifact_path(s.archive_path,'!group',day,'events-manifest').unlink()
    assert c.get(url+'download').status_code==503
    with archive.connect(postgres['owner']) as pg:pg.execute('UPDATE archive_audit SET cleaned_at=now()')
    assert c.get(url+'download').status_code==410
    assert c.get(url+'summary').json()['sha256']==trusted['sha256']


def test_health_reports_archive_failure_without_fake_success(gateway):
    c,app,s=gateway
    assert c.get('/health').status_code==200
    archive.record_failure(s.archive_ingest)
    result=c.get('/health')
    assert result.status_code==503 and result.json()['status']=='degraded'
