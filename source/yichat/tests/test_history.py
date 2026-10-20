import base64,json,time
from datetime import timedelta
import pytest
from backend import archive,history
from backend.db import connect
from test_gateway import gateway as browser_gateway


@pytest.fixture
def gateway(browser_gateway):
    c,app,s=browser_gateway
    c.cookies.clear();c.headers.pop('Origin',None)
    c.headers['Authorization']='Bearer session-token'
    return c,app,s


def test_web_transport_cannot_request_or_provide_history_packets(browser_gateway):
    c,app,s=browser_gateway;seed(s)
    for route,data in [('packets',{'ids':['$packet']}),('exchange',{'requests':[],'offers':[]})]:
        response=c.post('/api/history/'+route,json=data)
        assert response.status_code==403
        assert response.json()['code']=='YICHAT_APP_ONLY'
        # The same-origin Web client also supplies a Bearer token.
        response=c.post('/api/history/'+route,json=data,headers={'Authorization':'Bearer session-token'})
        assert response.status_code==403 and response.json()['code']=='YICHAT_APP_ONLY'
    assert c.get('/api/history/updates').status_code==200
    assert not app.state.history_relay.pending


def seed(s,old=False):
    at=int(time.time()*1000)-(32*86400000 if old else 2000)
    alice='@alice:chat.yitiao.org';bob='@bob:chat.yitiao.org'
    raw={'type':'m.room.encrypted','sender':alice,'origin_server_ts':at,'content':{'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'ONLY_NATIVE_CIPHERTEXT','session_id':'original-session','sender_key':'original-key'}}
    rows=[('$old-a','!direct',4,at-1000,json.dumps({'type':'m.room.member','state_key':alice,'sender':alice,'content':{'membership':'join'}})),('$old-b','!direct',5,at-1000,json.dumps({'type':'m.room.member','state_key':bob,'sender':bob,'content':{'membership':'join'}})),('$packet','!direct',6,at,json.dumps(raw))]
    archive.ingest_batch(s.archive_ingest,rows,6,int(time.time()*1000));history.record_rows(s,rows);history.backfill(s)
    with connect(s.ledger) as c:
        for mxid in (alice,bob):c.execute("INSERT OR REPLACE INTO yichat_memberships VALUES('!direct',?,'join','known',NULL,NULL,NULL)",(mxid,))
    return at


def clean_fixture(s,at):
    # Only dedicated fixture PG: simulate the established retention cleanup state.
    day=archive.utcday(at)
    with archive.connect(s.archive_sealer) as c:
        for spec in archive.SPECS:
            c.execute("INSERT INTO archives(room,day,version,spec,sha256,bytes,event_count,first_sequence,last_sequence,attachment_sha256,state,permission_spec,fence_sequence) VALUES(%s,%s,1,%s,%s,1,1,6,6,%s,'sealed',%s,6)",('!direct',day,spec,'a'*64,'b'*64,archive.PERMISSION_SPEC))
            c.execute('INSERT INTO archive_audit(room,day,version,spec) VALUES(%s,%s,1,%s)',('!direct',day,spec))
    with archive.connect(s.archive_cleanup) as c:
        c.execute("DELETE FROM events WHERE event_id='$packet'")
        c.execute("UPDATE archive_audit SET cleaned_at=now() WHERE room='!direct'")


def test_retained_packets_direct_from_server_and_authenticated_receipt(gateway):
    c,app,s=gateway;seed(s)
    update=c.get('/api/history/updates').json();row=next(r for r in update['records'] if r['event_id']=='$packet')
    response=c.post('/api/history/packets',json={'ids':['$packet']});assert response.status_code==200,response.text
    packet=response.json()['packets'][0];body=base64.b64decode(packet['raw']);assert archive.sha256(body)==row['sha256']
    assert json.loads(body)['type']=='m.room.encrypted' and packet['source']=='server'
    assert not app.state.history_relay.pending
    assert c.post('/api/history/packets',json={'ids':['$unknown']}).status_code==403


def test_missing_is_not_assumed_deleted_and_retained_range_never_asks_peers(gateway):
    c,app,s=gateway;seed(s)
    with archive.connect(s.archive_cleanup) as pg:pg.execute("DELETE FROM events WHERE event_id='$packet'")
    assert c.post('/api/history/packets',json={'ids':['$packet']}).status_code==503
    assert not app.state.history_relay.pending


def test_deleted_private_packet_peer_exchange_hash_and_membership(gateway):
    c,app,s=gateway;at=seed(s,old=True)
    original=c.post('/api/history/packets',json={'ids':['$packet']}).json()['packets'][0]
    clean_fixture(s,at)
    missing=c.post('/api/history/packets',json={'ids':['$packet']}).json()['packets'][0];key=missing['request']
    c.cookies.clear();c.headers['Authorization']='Bearer bob-token'
    asked=c.post('/api/history/exchange',json={}).json()['asked'];assert asked[0]['event_id']=='$packet'
    assert c.post('/api/history/exchange',json={'offers':[{'request':key,'raw':base64.b64encode(b'forged plaintext').decode()}]}).status_code==422
    offer={'offers':[{'request':key,'raw':original['raw']}]}
    with connect(s.ledger) as db:db.execute("UPDATE yichat_memberships SET membership='leave' WHERE room='!direct' AND mxid='@bob:chat.yitiao.org'")
    assert c.post('/api/history/exchange',json=offer).status_code==403
    with connect(s.ledger) as db:db.execute("UPDATE yichat_memberships SET membership='join' WHERE room='!direct' AND mxid='@bob:chat.yitiao.org'")
    c.headers['Authorization']='Bearer bob-token';accepted=c.post('/api/history/exchange',json=offer);assert accepted.status_code==200,accepted.text
    c.headers['Authorization']='Bearer session-token'
    result=c.post('/api/history/exchange',json={'requests':[key]}).json()['fulfilled'][0]
    assert result['raw']==original['raw'] and result['sha256']==original['sha256']
    assert result['source']=='peer' and not app.state.history_relay.pending


def test_peer_rejected_if_server_data_reappears_and_queue_expires(gateway):
    c,app,s=gateway;at=seed(s,old=True)
    original=c.post('/api/history/packets',json={'ids':['$packet']}).json()['packets'][0]
    clean_fixture(s,at);key=c.post('/api/history/packets',json={'ids':['$packet']}).json()['packets'][0]['request']
    raw=base64.b64decode(original['raw']);event=json.loads(raw)
    with archive.connect(s.archive_ingest) as pg:pg.execute('INSERT INTO events VALUES(%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s)',('$packet','!direct',6,at,archive.utcday(at),event['sender'],event['type'],raw,archive.sha256(raw)))
    c.cookies.clear();c.headers['Authorization']='Bearer bob-token'
    assert c.post('/api/history/exchange',json={'offers':[{'request':key,'raw':original['raw']}]}).status_code==409
    app.state.history_relay.pending[key]['expires']=0
    c.headers['Authorization']='Bearer session-token'
    result=c.post('/api/history/exchange',json={'requests':[key]}).json();assert not result['pending'] and not result['fulfilled']


def test_redaction_catchup_never_redistributes_original_body(gateway):
    c,app,s=gateway;at=seed(s)
    event={'type':'m.room.redaction','sender':'@alice:chat.yitiao.org','content':{'redacts':'$packet'}}
    rows=[('$redaction','!direct',7,at+1,json.dumps(event))]
    archive.ingest_batch(s.archive_ingest,rows,7,int(time.time()*1000));history.record_rows(s,rows)
    result=c.post('/api/history/packets',json={'ids':['$packet']}).json()['packets'][0]
    assert 'raw' not in result and result['tombstone']['event']['content']=={}
    assert result['tombstone']['redaction']['content']['redacts']=='$packet'
    update=c.get('/api/history/updates',params={'since':at-600000}).json()
    assert any(r['redacts']=='$packet' for r in update['records'])


def test_redaction_window_uses_server_accept_time_not_client_timestamp(env):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from backend.synapse_module import YiChatModule
    from fastapi import HTTPException
    instance=YiChatModule.__new__(YiChatModule);instance.s=env[0]
    original=SimpleNamespace(room_id='!room',is_state=lambda:False,content={},origin_server_ts=int(time.time()*1000)+86400000)
    store=SimpleNamespace(get_event=AsyncMock(return_value=original))
    instance.api=SimpleNamespace(_store=store,run_db_interaction=AsyncMock(return_value=int(time.time()*1000)-301000))
    event=SimpleNamespace(room_id='!room',sender='@alice:chat.yitiao.org',content={'redacts':'$original'})
    with pytest.raises(HTTPException) as error:asyncio.run(instance.validate_redaction(event))
    assert error.value.detail['code']=='YICHAT_REDACTION_WINDOW'
    instance.api.run_db_interaction.return_value=int(time.time()*1000)-1000
    asyncio.run(instance.validate_redaction(event))
    original.content={history.BUSINESS:{'kind':'gift','id':'0'*32}}
    with pytest.raises(HTTPException) as error:asyncio.run(instance.validate_redaction(event))
    assert error.value.detail['code']=='YICHAT_BUSINESS_IMMUTABLE'


def test_returning_member_receives_redaction_of_original_visible_before_leaving(gateway):
    c,app,s=gateway;at=seed(s)
    rows=[('$away','!direct',7,at+1,json.dumps({'type':'m.room.member','sender':'@bob:chat.yitiao.org','state_key':'@bob:chat.yitiao.org','content':{'membership':'leave'}})),('$while-away','!direct',8,at+2,json.dumps({'type':'m.room.redaction','sender':'@alice:chat.yitiao.org','content':{'redacts':'$packet'}})),('$back','!direct',9,at+3,json.dumps({'type':'m.room.member','sender':'@bob:chat.yitiao.org','state_key':'@bob:chat.yitiao.org','content':{'membership':'join'}}))]
    archive.ingest_batch(s.archive_ingest,rows,9,int(time.time()*1000));history.record_rows(s,rows)
    c.cookies.clear();c.headers['Authorization']='Bearer bob-token'
    result=c.get('/api/history/updates',params={'since':at+1}).json()
    assert any(row['event_id']=='$while-away' for row in result['records'])
    packet=c.post('/api/history/packets',json={'ids':['$while-away']}).json()['packets'][0]
    assert packet['source']=='server_control' and 'raw' not in packet
    assert packet['tombstone']['event']['content']['redacts']=='$packet'
