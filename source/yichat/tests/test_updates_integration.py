import json,time,secrets,asyncio
from urllib.parse import quote
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import connect
from backend.security import digest
from backend import archive,server_messages
from test_synapse_integration import synapse

def test_real_receipt_group_api_and_server_receipt_outbox(synapse,monkeypatch):
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
  bob_key=bob_client.post('/api/developer/keys',json={'name':'reader','scope':'read'}).json();bob=bob_key['key']
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
  def preference():
   r=bob_client.get('/api/conversations/preferences');assert r.status_code==200,r.text
   return next(p for p in r.json() if p['room']==room)
  assert preference()['read_event']==eid and preference()['unread_count']==0
  # Full account marker is persisted in Synapse, not only in this device's UI.
  synced=h.get(s.synapse+'/_matrix/client/v3/sync',headers=rb,params={'timeout':0}).json()['rooms']['join'][room]
  assert synced.get('unread_notifications',{}).get('notification_count',0)==0
  assert any(e.get('type')=='m.fully_read' and e.get('content',{}).get('event_id')==eid for e in synced.get('account_data',{}).get('events',[]))
  r=a.post('/open/v1/conversations/'+quote(room,safe='')+'/messages',json={'text':'unread after unsubscribe'});assert r.status_code==200,r.text
  until=time.monotonic()+10
  while preference()['unread_count']<1 and time.monotonic()<until:time.sleep(.1)
  assert preference()['read_event']==eid and preference()['unread_count']>=1
  # Account-wide subscriptions count too, even without reading a delivered frame.
  with b.websocket_connect('/open/v1/stream') as ws:
   assert ws.receive_json()['type']=='ready'
   time.sleep(.3)
   r=a.post('/open/v1/conversations/'+quote(room,safe='')+'/messages',json={'text':'account subscription without acknowledgements'});assert r.status_code==200,r.text;all_eid=r.json()['event_id']
   until=time.monotonic()+10
   while preference()['read_event']!=all_eid and time.monotonic()<until:time.sleep(.2)
   assert preference()['read_event']==all_eid and preference()['unread_count']==0
   seen=False
   while time.monotonic()<until:
    data=h.get(s.synapse+'/_matrix/client/v3/sync',headers={'Authorization':'Bearer '+tokens['alice']['raw']},params={'timeout':0,**({'since':since} if since else {})}).json();since=data.get('next_batch',since)
    events=data.get('rooms',{}).get('join',{}).get(room,{}).get('ephemeral',{}).get('events',[])
    if any(users['bob']['mxid'] in e.get('content',{}).get(all_eid,{}).get('m.read',{}) for e in events):seen=True;break
    time.sleep(.2)
   assert seen,'Account-wide subscription did not publish a real public read receipt'
  time.sleep(1.1)
  r=bob_client.patch('/api/developer/keys/'+bob_key['id'],json={'scope':'read_write'});assert r.status_code==200,r.text
  time.sleep(1.1)
  r=a.patch('/open/v1/groups/'+quote(group,safe=''),json={'name':'New Group'});assert r.status_code==200,r.text
  assert h.get(s.synapse+'/_matrix/client/v3/rooms/'+quote(group,safe='')+'/state/m.room.name',headers={'Authorization':'Bearer '+tokens['alice']['raw']}).json()['name']=='New Group'
  # Reuse the same physical upload in a second real Synapse room and archive it.
  import hashlib
  uploaded=c.post('/api/files/'+quote(room,safe=''),content=b'real cross-room reused attachment');assert uploaded.status_code==200,uploaded.text
  fid=uploaded.json()['id'];reused=c.post('/api/files/'+quote(group,safe='')+'/reuse',json={'sha256':hashlib.sha256(b'real cross-room reused attachment').hexdigest()});assert reused.status_code==200,reused.text
  r=c.put('/_matrix/client/v3/rooms/'+quote(group,safe='')+'/send/m.room.message/real-reuse',json={'msgtype':'m.file','body':'reused.txt','url':uploaded.json()['url'],'org.yitiao.attachments':[fid]});assert r.status_code==200,r.text
  event=r.json()['event_id'];found=None;until=time.monotonic()+10
  while time.monotonic()<until:
   with archive.connect(s.archive_reader) as pg:found=pg.execute('SELECT 1 FROM attachment_references WHERE event_id=%s AND file=%s AND room=%s',(event,fid,group)).fetchone()
   if found:break
   time.sleep(.2)
  assert found,'Cross-room reference was not archived with its original file ID'
  from backend import sharing
  from types import SimpleNamespace
  monkeypatch.setattr(sharing,'time',SimpleNamespace(time=lambda:time.time()+301))
  r=a.post('/open/v1/conversations/'+quote(room,safe='')+'/forward',json={'message_ids':[eid],'target':group,'identity':'avatar','txn_id':'real-api-record'})
  assert r.status_code==200,r.text;forwarded=r.json();until=time.monotonic()+10
  while c.get('/api/public-records/'+forwarded['hash']).status_code==404 and time.monotonic()<until:time.sleep(.2)
  assert c.get('/api/public-records/'+forwarded['hash']).status_code==200
  monkeypatch.undo()
  # Publication opens only when the real Matrix record message is accepted.
  from backend import public_records
  with archive.connect(s.archive_reader) as pg:source=pg.execute('SELECT body FROM events WHERE event_id=%s',(eid,)).fetchone()
  publication=public_records.prepare(app,s,users['alice'],room,[json.loads(bytes(source['body']))],'both')
  with connect(s.db) as db:db.execute('INSERT OR IGNORE INTO shared_record_rooms VALUES(?,?)',(publication['hash'],group))
  assert c.get('/api/public-records/'+publication['hash']).status_code==404
  message={'msgtype':'m.text','body':publication['url'],'org.yitiao.record':publication['hash'],'org.yitiao.record_json':publication['canonical']}
  sent=c.put('/_matrix/client/v3/rooms/'+quote(group,safe='')+'/send/m.room.message/real-record-activate',json=message);assert sent.status_code==200,sent.text
  until=time.monotonic()+8;published=False
  while time.monotonic()<until:
   response=c.get('/api/public-records/'+publication['hash'])
   if response.status_code==200:published=True;break
   time.sleep(.5)
  assert published,'Accepted record message did not activate its public URL'
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
