import json,time,secrets,asyncio
from urllib.parse import quote
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import connect
from backend.security import digest
from backend import archive,server_messages
from test_synapse_integration import synapse

def test_real_receipt_group_api_and_server_receipt_outbox(synapse):
 s,_,users,h,*_=synapse;app=create_app(s)
 with TestClient(app) as c:
  async def seed():
   tokens={}
   for name in ('alice','bob'):
    who=users[name];m=app.state.matrix
    with connect(s.db) as db:password=app.state.cipher.decrypt(db.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()[0].encode()).decode()
    await m.provision(who['mxid'],password,name);auth=await m.login(who['mxid'],password);gateway=secrets.token_urlsafe(32)
    with connect(s.db) as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(gateway),who['subject'],name,who['sid'],who['mxid'],auth['device_id'],app.state.cipher.encrypt(auth['access_token'].encode()).decode(),time.time()+3600))
    tokens[name]={'gateway':gateway,'raw':auth['access_token']}
   return tokens
  tokens=c.portal.call(seed);c.headers['Origin']=s.origin;c.headers['Authorization']='Bearer '+tokens['alice']['gateway'];c.cookies.set('yc_session',tokens['alice']['gateway'])
  r=c.post('/api/rooms',json={'kind':'direct','users':['bob']});assert r.status_code==200,r.text;room=r.json()['room_id'];p='/_matrix/client/v3/rooms/'+quote(room,safe='')
  prepared=c.post('/api/rooms/'+quote(room,safe='')+'/prepare',json={});assert prepared.status_code==200,prepared.text
  rb={'Authorization':'Bearer '+tokens['bob']['raw']};assert h.post(s.synapse+p+'/join',headers=rb,json={}).status_code==200
  r=c.post('/api/rooms',json={'kind':'group','users':['bob'],'name':'Old Group'});assert r.status_code==200,r.text;group=r.json()['room_id']
  alice=c.post('/api/developer/keys',json={'name':'sender','scope':'read_write'}).json()['key']
  bob_client=TestClient(app);bob_client.cookies.set('yc_session',tokens['bob']['gateway']);bob_client.headers['Origin']=s.origin
  bob=bob_client.post('/api/developer/keys',json={'name':'reader','scope':'read_write'}).json()['key']
  class API:
   def __init__(self,key):self.key=key
   def post(self,path,**kwargs):return c.post(path,headers={'Authorization':'Bearer '+self.key},**kwargs)
   def patch(self,path,**kwargs):return c.patch(path,headers={'Authorization':'Bearer '+self.key},**kwargs)
   def websocket_connect(self,path):return c.websocket_connect(path,headers={'Authorization':'Bearer '+self.key})
  a=API(alice);b=API(bob)
  with b.websocket_connect('/open/v1/stream?conversation='+quote(room,safe='')) as ws:
   assert ws.receive_json()['type']=='ready'
   r=a.post('/open/v1/conversations/'+quote(room,safe='')+'/messages',json={'text':'read through websocket'});assert r.status_code==200,r.text;eid=r.json()['event_id']
   assert ws.receive_json()['message']['id']==eid
   until=time.monotonic()+8;seen=False;since=None
   while time.monotonic()<until:
    data=h.get(s.synapse+'/_matrix/client/v3/sync',headers={'Authorization':'Bearer '+tokens['alice']['raw']},params={'timeout':0,**({'since':since} if since else {})}).json()
    since=data.get('next_batch',since)
    events=data.get('rooms',{}).get('join',{}).get(room,{}).get('ephemeral',{}).get('events',[])
    if any(users['bob']['mxid'] in e.get('content',{}).get(eid,{}).get('m.read',{}) for e in events):seen=True;break
    time.sleep(.2)
   assert seen,'Real Synapse did not publish the public read receipt'
  time.sleep(1.1)
  r=a.patch('/open/v1/groups/'+quote(group,safe=''),json={'name':'New Group'});assert r.status_code==200,r.text
  assert h.get(s.synapse+'/_matrix/client/v3/rooms/'+quote(group,safe='')+'/state/m.room.name',headers={'Authorization':'Bearer '+tokens['alice']['raw']}).json()['name']=='New Group'
  f=app.state.funds
  with connect(s.ledger) as db:f.bank(db);f.move(db,None,users['alice']['subject'],f.y.units('5'),'isolated-fixture-credit')
  time.sleep(1.1)
  r=a.post('/open/v1/conversations/'+quote(room,safe='')+'/transfers',json={'amount':'1','key':'real-transfer-test-00001'});assert r.status_code==200,r.text;oid=r.json()['id']
  time.sleep(1.1)
  r=b.post('/open/v1/transfers/'+oid+'/receive',json={});assert r.status_code==200,r.text
  # No browser session: the server announcement's exact grant admits this event.
  with connect(s.db) as db:db.execute("UPDATE sessions SET revoked=1 WHERE username='bob'")
  c.portal.call(server_messages.tick,app)
  until=time.monotonic()+8;receipt=None
  while time.monotonic()<until:
   with connect(s.db) as db:receipt=db.execute('SELECT event FROM server_announcements WHERE id=?',('receipt:'+oid,)).fetchone()
   if receipt and receipt[0]:break
   c.portal.call(server_messages.tick,app);time.sleep(.2)
  assert receipt and receipt[0]
  with archive.connect(s.archive_reader) as pg:
   until=time.monotonic()+5
   while time.monotonic()<until:
    row=pg.execute('SELECT body FROM events WHERE event_id=%s',(receipt[0],)).fetchone()
    if row:break
    time.sleep(.1)
  assert row and json.loads(bytes(row['body']))['content']['org.yitiao.share']['type']=='receipt'
  f.reconcile()
