"""No Firebase credentials or production accounts: HTTP mocked, isolated DBs."""
import json,time
from unittest.mock import AsyncMock
import httpx,pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import push
from backend.db import connect
from backend.security import digest

@pytest.fixture
def fixture(env):
    s,_,users=env;who=users['alice']|{'device':'PUSH_TEST','token_hash':digest('test-session')}
    app=FastAPI();app.state.cipher=Fernet(s.session_key.encode())
    sender=push.FCMSender(s,None);sender.credentials={'test':True};app.state.push=sender
    push.install(app,s,lambda _:who)
    with connect(s.db) as c:
        c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(who['token_hash'],who['subject'],'alice','sid-alice',who['mxid'],who['device'],'test',time.time()+3600))
        c.execute('INSERT INTO native_devices VALUES(?,?,?,?,?)',('proof',who['subject'],who['mxid'],who['device'],time.time()))
        c.execute("INSERT INTO rooms VALUES('!test:chat.yitiao.org','group',?,NULL,30)",(who['mxid'],))
    with TestClient(app) as client:yield s,who,sender,client

def receipt(s,sequence,event='$test',room='!test:chat.yitiao.org'):
    with connect(s.db) as c:
        c.execute('INSERT INTO history_receipts VALUES(?,?,?,?,?,?,?,?,?,?)',(event,room,sequence,int(time.time()*1000),'@bob:chat.yitiao.org','m.room.encrypted',int(time.time()*1000),'a'*64,None,None))

def register(client,token='fixture_FCM_token_01234567890',enabled=True):
    return client.post('/api/push/device',json={'token':token,'enabled':enabled})

def test_payload_is_high_priority_data_only_and_direct_boot_safe():
    value=push.payload('opaque-token',{'room':'!room','kind':'group','event':'$event','nickname':'SECRET','body':'SECRET','name':'SECRET'})
    assert value=={'message':{'token':'opaque-token','android':{'priority':'high','direct_boot_ok':True,'ttl':'86400s'},'data':{'conversation_id':'!room','conversation_type':'group','message_id':'$event'}}}
    assert 'SECRET' not in json.dumps(value) and 'notification' not in value['message']
    assert push.payload('token',{'room':'!room','kind':'direct','event':'$event'})['message']['data']['conversation_type']=='private'

def test_registration_encrypts_token_and_refresh_preserves_cursor(fixture):
    s,who,sender,client=fixture
    assert register(client).status_code==200
    receipt(s,1)
    assert register(client,'refreshed_FCM_token_01234567890').status_code==200
    with connect(s.db) as c:row=c.execute('SELECT * FROM push_devices').fetchone()
    assert row['cursor']==0
    assert 'refreshed' not in row['token']
    assert sender.cipher.decrypt(row['token'].encode()).decode()=='refreshed_FCM_token_01234567890'
    assert register(client,enabled=False).status_code==200
    with connect(s.db) as c:assert c.execute('SELECT enabled FROM push_devices').fetchone()[0]==0

def test_registration_requires_native_and_configured_sender(fixture):
    s,who,sender,client=fixture
    sender.credentials=None
    assert register(client).status_code==503
    sender.credentials={'test':True}
    with connect(s.db) as c:c.execute('DELETE FROM native_devices')
    assert register(client).status_code==403

@pytest.mark.parametrize('data',[{'token':'x','enabled':True},{'token':'fixture_FCM_token_01234567890','enabled':1},{'token':'fixture_FCM_token_01234567890','enabled':True,'name':'secret'}])
def test_registration_rejects_bad_schema(fixture,data):
    assert fixture[3].post('/api/push/device',json=data).status_code==400

@pytest.mark.asyncio
async def test_dispatch_retries_without_advancing_cursor_and_discards_expired_token(fixture):
    s,who,sender,client=fixture
    register(client);receipt(s,1)
    sender.send=AsyncMock(side_effect=RuntimeError('must never be logged'))
    await sender.drain()
    with connect(s.db) as c:
        row=c.execute('SELECT * FROM push_devices').fetchone();assert row['cursor']==0 and row['failures']==1
        c.execute('UPDATE push_devices SET next_attempt=0')
    sender.send=AsyncMock(return_value='sent');await sender.drain()
    sender.send.assert_awaited_once()
    with connect(s.db) as c:
        assert c.execute('SELECT cursor FROM push_devices').fetchone()[0]==1
        c.execute('UPDATE push_devices SET next_attempt=0')
    receipt(s,2,'$next');sender.send=AsyncMock(return_value='expired');await sender.drain()
    with connect(s.db) as c:assert c.execute('SELECT count(*) FROM push_devices').fetchone()[0]==0

@pytest.mark.parametrize('mode',['muted','archived','read','left','revoked','pending-membership','redacted'])
@pytest.mark.asyncio
async def test_dispatch_respects_preferences_membership_and_revocation(fixture,mode):
    s,who,sender,client=fixture
    register(client);receipt(s,1)
    if mode in ('muted','archived','read'):
        with connect(s.db) as c:
            c.execute('INSERT INTO conversation_preferences(subject,room,muted,archived,read_sequence) VALUES(?,?,?,?,?)',(who['subject'],'!test:chat.yitiao.org',int(mode=='muted'),int(mode=='archived'),int(mode=='read')))
    elif mode=='left':
        with connect(s.ledger) as c:c.execute("UPDATE yichat_memberships SET membership='leave' WHERE mxid=?",(who['mxid'],))
    elif mode=='pending-membership':
        with connect(s.ledger) as c:c.execute("UPDATE yichat_memberships SET pending_event='$pending' WHERE mxid=?",(who['mxid'],))
    elif mode=='redacted':
        with connect(s.db) as c:c.execute("INSERT INTO history_receipts VALUES('$redaction','!test:chat.yitiao.org',2,?,'@bob:chat.yitiao.org','m.room.redaction',?,'hash','$test',NULL)",(int(time.time()*1000),int(time.time()*1000)))
    else:
        with connect(s.ledger) as c:c.execute("UPDATE yidt_sessions SET revoked=1 WHERE sid='sid-alice'")
    sender.send=AsyncMock(return_value='sent');await sender.drain();sender.send.assert_not_awaited()

@pytest.mark.asyncio
async def test_http_v1_does_not_log_or_forward_server_payload(fixture,caplog):
    s,who,sender,client=fixture;s.fcm_project='isolated-fixture';sender.bearer=AsyncMock(return_value='test-oauth')
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(404,json={'error':{'details':[{'@type':'type.googleapis.com/google.firebase.fcm.v1.FcmError','errorCode':'UNREGISTERED'}]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sender.http=http
        assert await sender.send('fixture-token',{'room':'!test','kind':'direct','event':'$event'})=='expired'
    request=requests[0]
    assert str(request.url)=='https://fcm.googleapis.com/v1/projects/isolated-fixture/messages:send'
    assert request.headers['Authorization']=='Bearer test-oauth'
    assert set(json.loads(request.content)['message']['data'])=={'conversation_id','conversation_type','message_id'}
    assert 'fixture-token' not in caplog.text


def test_unread_notification_ids_merge_bursts_and_exclude_read_or_muted(fixture):
    from backend.conversations import unread_notification_events
    s,who,sender,client=fixture
    receipt(s,1,'$first');receipt(s,2,'$second')
    room='!test:chat.yitiao.org'
    assert unread_notification_events(s,who,room,'$second')==['$first','$second']
    with connect(s.db) as c:c.execute('INSERT INTO conversation_preferences(subject,room,read_sequence) VALUES(?,?,1)',(who['subject'],room))
    assert unread_notification_events(s,who,room,'$second')==['$second']
    assert unread_notification_events(s,who,room,'$first')==[]
    with connect(s.db) as c:c.execute('UPDATE conversation_preferences SET muted=1')
    assert unread_notification_events(s,who,room,'$second')==[]

@pytest.mark.asyncio
async def test_preview_negotiation_keeps_old_clients_compatible_and_sends_verified_text(fixture,monkeypatch):
    from test_notification_summary import seed
    s,who,sender,client=fixture
    assert register(client).status_code==200
    seed(s,1,'$preview','对方输入的真实文字')
    sender.send=AsyncMock(return_value='sent')
    await sender.drain()
    assert 'preview' not in sender.send.call_args.args[1]
    assert client.post('/api/push/device',json={'token':'fixture_FCM_token_01234567890','enabled':True,'preview_version':1}).status_code==200
    seed(s,2,'$next','第二条真实文字')
    sender.send.reset_mock();await sender.drain()
    hint=sender.send.call_args.args[1]
    assert push.payload('token',hint)['message']['data']['message_preview']=='第二条真实文字'
    with connect(s.db) as c:assert c.execute('SELECT preview_version FROM push_devices').fetchone()[0]==1


@pytest.mark.parametrize('version',[True,2,-1,'1'])
def test_preview_version_is_validated_without_registering_incompatible_devices(fixture,version):
    assert fixture[3].post('/api/push/device',json={'token':'fixture_FCM_token_01234567890','enabled':True,'preview_version':version}).status_code==400


def test_existing_push_device_migration_preserves_token_and_unread_cursor(tmp_path):
    old=push.SCHEMA.replace('preview_version INTEGER NOT NULL DEFAULT 0,','')
    with connect(tmp_path/'old.db') as c:
        c.executescript(old)
        c.execute("INSERT INTO push_devices(subject,device,session_hash,token_hash,token,enabled,cursor) VALUES('uid','device','session','hash','encrypted',1,42)")
        push.migrate(c);push.migrate(c)
        row=c.execute('SELECT * FROM push_devices').fetchone()
        assert row['preview_version']==0 and row['cursor']==42 and row['token']=='encrypted'

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['disabled','muted'])
async def test_access_change_while_building_preview_prevents_send(fixture,monkeypatch,mode):
    from test_notification_summary import seed
    s,who,sender,client=fixture
    client.post('/api/push/device',json={'token':'fixture_FCM_token_01234567890','enabled':True,'preview_version':1})
    seed(s,1,'$private','不应被推送的正文')
    def changed(*args):
        with connect(s.db) as c:
            if mode=='disabled':c.execute('UPDATE push_devices SET enabled=0')
            else:c.execute('INSERT INTO conversation_preferences(subject,room,muted) VALUES(?,?,1)',(who['subject'],'!test:chat.yitiao.org'))
        return '不应被推送的正文'
    monkeypatch.setattr(push,'event_preview',changed)
    sender.send=AsyncMock(return_value='sent');await sender.drain();sender.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrupt_preview_falls_back_to_route_without_logging_body(fixture,caplog):
    from test_notification_summary import seed
    s,who,sender,client=fixture
    client.post('/api/push/device',json={'token':'fixture_FCM_token_01234567890','enabled':True,'preview_version':1})
    seed(s,1,'$private','私密正文不得进入错误日志')
    with connect(s.db) as c:c.execute('UPDATE history_receipts SET sha256=?',('0'*64,))
    sender.send=AsyncMock(return_value='sent');await sender.drain()
    assert 'preview' not in sender.send.call_args.args[1]
    assert '私密正文' not in caplog.text
