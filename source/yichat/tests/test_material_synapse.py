"""Real Synapse admission, original document publishing and source expiry v2."""
import json,time,secrets,hashlib
from urllib.parse import quote
import pytest
from fastapi.testclient import TestClient
from backend import archive,materials,history,protocol_retention
from backend.app import create_app
from backend.db import connect
from backend.security import digest
from test_synapse_integration import synapse

@pytest.fixture
def material_synapse(env,request):
 env[0].material_protocol=True
 return request.getfixturevalue('synapse')

def test_real_material_publisher_guards_receipts_expiry_and_restart(material_synapse,monkeypatch):
 s,f,users,http,process,logpath,start,stop=material_synapse
 app=create_app(s)
 with TestClient(app) as c:
  async def seed():
   result={}
   for name in ('alice','bob'):
    who=users[name];mx=app.state.matrix
    with connect(s.db) as db:enc=db.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()[0]
    password=app.state.cipher.decrypt(enc.encode()).decode();await mx.provision(who['mxid'],password,name);auth=await mx.login(who['mxid'],password);token=secrets.token_urlsafe(32)
    with connect(s.db) as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),who['subject'],name,who['sid'],who['mxid'],auth['device_id'],app.state.cipher.encrypt(auth['access_token'].encode()).decode(),time.time()+3600))
    result[name]={'gateway':token,'raw':auth['access_token']}
   return result
  tokens=c.portal.call(seed);c.headers.update({'Origin':s.origin,'Authorization':'Bearer '+tokens['alice']['gateway']});raw={'Authorization':'Bearer '+tokens['alice']['raw']}
  result=c.post('/api/rooms',json={'kind':'direct','users':['bob']});assert result.status_code==200,result.text
  room=result.json()['room_id'];rp='/_matrix/client/v3/rooms/'+quote(room,safe='');assert c.post('/api/rooms/'+quote(room,safe='')+'/prepare',json={}).status_code==200
  from backend import previews
  url='https://yitiao.org/games/parachute/share/'+'p'*32+'/'
  monkeypatch.setattr(previews,'metadata',lambda *_:{'type':'game','url':url,'title':'Original','data':{'status':'jumped','exit_height':22.25}})
  direct=http.put(s.synapse+rp+'/send/m.room.message/direct',headers=raw,json={'msgtype':'m.text','body':url});assert direct.status_code==403,direct.text
  sent=c.put(rp+'/send/m.room.message/v2-one',json={'msgtype':'m.text','body':url});assert sent.status_code==200,sent.text;eid=sent.json()['event_id']
  for _ in range(100):
   try:state=materials.state(s,eid);break
   except Exception:time.sleep(.05)
  else:pytest.fail('Accepted material receipt was not persisted')
  assert not state['finalized'] and state['room_id']==room
  doc=c.get('/api/materials/'+state['id'],params={'event':eid});assert doc.status_code==200,doc.text
  assert hashlib.sha256(doc.content).hexdigest()==state['payload_sha256'] and json.loads(doc.content)['previews'][0]['data']['exit_height']==22.25
  accepted=http.get(s.synapse+rp+'/event/'+quote(eid,safe=''),headers=raw);content=accepted.json()['content'];assert content[materials.KEY]['id']==state['id']
  forged=http.put(s.synapse+rp+'/send/m.room.message/stolen',headers=raw,json=content);assert forged.status_code==403,forged.text
  again=c.put(rp+'/send/m.room.message/v2-one',json={'msgtype':'m.text','body':url});assert again.json()['event_id']==eid
  # Authenticated service cannot censor unstaged/new/state messages.
  service={'Authorization':'Bearer '+s.admin_token};resource=s.synapse+'/_synapse/client/yichat/material-expiry'
  refused=http.post(resource,headers=service,json={'events':[eid]});assert refused.status_code==200 and refused.json()['expired']==[],refused.text
  assert http.post(resource,headers=raw,json={'events':[eid]}).status_code in (401,403)
  # Simulate a retained day's age at the test storage boundary, then exercise
  # the real Synapse expiry primitive and replication/cache invalidation.
  at=int(time.time()*1000)-8*86400000;day=str(archive.utcday(at))
  with connect(s.db) as db:
   db.execute('UPDATE history_receipts SET received_ms=? WHERE event_id=?',(at,eid));db.execute('INSERT OR REPLACE INTO packet_day_roots VALUES(?,?,?,?,?)',(room,day,'0'*64,1,at));db.execute("INSERT INTO protocol_expiry_jobs VALUES(?,?,?,'pending')",(eid,room,'expiry'))
  expired=http.post(resource,headers=service,json={'events':[eid]});assert expired.status_code==200,expired.text
  assert not http.get(s.synapse+rp+'/event/'+quote(eid,safe=''),headers=raw).json().get('content',{}).get(materials.KEY)
  assert protocol_retention.finish_entities(s)==1
  with archive.connect(s.archive_reader) as pg:assert not pg.execute('SELECT 1 FROM events WHERE event_id=%s',(eid,)).fetchone()
  with connect(s.db) as db:assert db.execute('SELECT sha256 FROM history_receipts WHERE event_id=?',(eid,)).fetchone()[0]==''
  stop(process);process=start();time.sleep(.5)
  with archive.connect(s.archive_reader) as pg:assert not pg.execute('SELECT 1 FROM events WHERE event_id=%s',(eid,)).fetchone(), 'Expired body was reingested after source restart'
