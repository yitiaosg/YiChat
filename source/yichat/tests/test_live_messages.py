import base64,json,time,hashlib
import pytest
from backend import archive
from backend.db import connect
from test_gateway import gateway as browser_gateway
from test_material_protocol import accepted

@pytest.fixture
def gateway(browser_gateway):
 c,app,s=browser_gateway;c.cookies.clear();c.headers.pop('Origin',None);c.headers['Authorization']='Bearer session-token'
 with connect(s.db) as db:db.execute("UPDATE room_modes SET encryption='plain' WHERE room='!direct'")
 return c,app,s

def test_recent_verifies_latest_canonical_packets_without_advancing_a_bulk_cursor(gateway):
 c,app,s=gateway;accepted(s,{'msgtype':'m.text','body':'最新即时保存'},eid='$latest',at=int(time.time()*1000)-20,seq=40)
 r=c.post('/api/history/recent',json={'room':'!direct','limit':1});assert r.status_code==200,r.text
 row=r.json()['records'][0];assert row['receipt']['event_id']=='$latest';raw=base64.b64decode(row['packet']['raw']);assert hashlib.sha256(raw).hexdigest()==row['receipt']['sha256'];assert json.loads(raw)['content']['body']=='最新即时保存'
 result=c.post('/api/history/recent',json={'room':'!direct','ids':['$latest','$not-archived-yet']}).json();assert result['pending']==['$not-archived-yet'] and len(result['records'])==1

def test_recent_denies_other_rooms_and_web_transport(gateway,browser_gateway):
 c,app,s=gateway;accepted(s,{'msgtype':'m.text','body':'private'},eid='$private')
 assert c.post('/api/history/recent',json={'room':'!group','ids':['$private']}).status_code==403
 assert c.post('/api/history/recent',json={'room':'!unknown'}).status_code==403
 assert c.post('/api/history/recent',json={'room':'!direct','ids':['$private']},headers={'Origin':s.origin}).status_code==403
 assert c.post('/api/history/recent',json={'room':'!direct','ids':['$private']*21}).status_code==400

def test_recent_rejects_server_corruption_and_propagates_withdrawals(gateway,postgres):
 c,app,s=gateway;accepted(s,{'msgtype':'m.text','body':'original'},eid='$latest')
 with archive.connect(postgres['owner']) as pg:pg.execute('UPDATE events SET body=%s WHERE event_id=%s',(b'{}\n','$latest'))
 assert c.post('/api/history/recent',json={'room':'!direct','ids':['$latest']}).status_code==503
 with connect(s.db) as db:db.execute('INSERT INTO history_receipts VALUES(?,?,?,?,?,?,?,?,?,?)',('$withdraw','!direct',41,int(time.time()*1000),'@alice:chat.yitiao.org','m.room.redaction',int(time.time()*1000),'a'*64,'$latest',None))
 result=c.post('/api/history/recent',json={'room':'!direct','ids':['$latest']});assert result.status_code==200,result.text;assert result.json()['records'][0]['packet']['tombstone'];assert not result.json()['records'][0]['packet'].get('raw')
