import json,time,base64,hashlib,copy
from datetime import datetime,timezone,timedelta
import pytest
from fastapi import HTTPException
from backend import materials,day_packets,archive,history
from backend.db import connect
from test_gateway import gateway
from test_assets import cards

def who(s,name='alice'):
 with connect(s.db) as c:return dict(c.execute('SELECT subject,mxid,username FROM identities WHERE username=?',(name,)).fetchone())
def accepted(s,content,eid='$material',at=None,seq=40):
 at=at or int(time.time()*1000)-600001
 raw=json.dumps({'event_id':eid,'room_id':'!direct','type':'m.room.message','sender':who(s)['mxid'],'origin_server_ts':at,'content':content})
 rows=[(eid,'!direct',seq,at,raw)];archive.ingest_batch(s.archive_ingest,rows,seq,int(time.time()*1000));history.record_rows(s,rows)
 return at

def test_frozen_card_document_survives_source_disappearance_and_checks_do_not_repeat(gateway,monkeypatch):
 c,app,s=gateway;s.material_protocol=True
 from backend import previews
 url='https://yitiao.org/ycards/card/'+'b'*32+'/alice/'
 metadata={'type':'card','url':url,'title':'原始卡片','data':{'name':'不会随出售消失','design':{'layers':[{'image':'data:image/png;base64,AAAA'}]}}}
 monkeypatch.setattr(previews,'metadata',lambda *args:copy.deepcopy(metadata))
 content=c.portal.call(materials.prepare,app,s,who(s),'!direct',{'msgtype':'m.text','body':url})
 assert content['body']=='分享闪卡' and content[materials.KEY]['kind']=='share'
 accepted(s,content);monkeypatch.setattr(previews,'metadata',lambda *args:None)
 row=materials.state(s,'$material');assert row['finalized']
 raw=(materials.directory(s)/row['id']).read_text();assert json.loads(raw)['previews'][0]['data']['name']=='不会随出售消失'
 r=c.post('/api/materials/check',json={'messages':[{'event_id':'$material','payload_sha256':row['payload_sha256'],'sha256':row['sha256']}]});assert r.status_code==200,r.text
 assert r.json()['messages'][0]['download'] is None and r.json()['messages'][0]['state']=='ok'
 changed=c.post('/api/materials/check',json={'messages':[{'event_id':'$material','payload_sha256':'0'*64,'sha256':'0'*64}]}).json()['messages'][0];assert changed['state']=='replace'
 downloaded=c.get(changed['download']);assert downloaded.status_code==200 and hashlib.sha256(downloaded.content).hexdigest()==row['payload_sha256']
 assert c.post('/api/materials/$material/confirm',json={'payload_sha256':row['payload_sha256'],'sha256':row['sha256']}).json()['state']=='ok'
 assert c.post('/api/materials/$material/confirm',json={'payload_sha256':'0'*64,'sha256':row['sha256']}).json()['state']=='mismatch'
 assert materials.cleanup(s,now_ms=row['expires_ms']+1)==1
 assert not (materials.directory(s)/row['id']).exists()
 with connect(s.db) as db:assert not db.execute('SELECT 1 FROM material_events').fetchone()


def text_packet(day=None):
 day=day or (datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat();at=archive.day_bounds(day)[0]+500000
 raw=archive.canonical({'event_id':'$text','room_id':'!r','sender':'@a:x','type':'m.room.message','origin_server_ts':at,'content':{'msgtype':'m.text','body':'原始文字 👋 1.0'}}).decode()
 return day_packets.header('!r',day)|{'complete':True,'entries':[{'event_id':'$text','room_id':'!r','sender':'@a:x','type':'m.room.message','received_ms':str(at),'sequence':'1','raw':raw,'files':[]}]}

def test_day_hash_binds_actual_content_timestamps_order_and_empty_days():
 p=text_packet();h=day_packets.packet_hash(p)
 for mutate in [lambda x:x['entries'][0].update(raw=x['entries'][0]['raw'].replace('原始文字','错误文字')),lambda x:x['entries'][0].update(received_ms=str(int(x['entries'][0]['received_ms'])+1)),lambda x:x.update(room_id='!other')]:
  q=copy.deepcopy(p);mutate(q)
  try:assert day_packets.packet_hash(q)!=h
  except ValueError:pass
 empty=day_packets.header('!r',p['day'])|{'complete':True,'entries':[]};assert day_packets.packet_hash(empty)!=h
 assert day_packets.immutable_ms(p['day'])==archive.day_bounds(p['day'])[1]+300000
 with pytest.raises(HTTPException):day_packets.day_range('2026-01-01','2026-01-08')


def test_media_verification_hashes_uploaded_bytes_instead_of_declared_hash():
 p=text_packet();data=b'actual image bytes';fid='a'*48;f={'id':fid,'bytes':str(len(data)),'sha256':hashlib.sha256(data).hexdigest(),'data':base64.b64encode(data).decode()};p['entries'][0]['files']=[f]
 original=day_packets.packet_hash(p);bad=copy.deepcopy(p);bad['entries'][0]['files'][0]['data']=base64.b64encode(b'bad image bytes').decode()
 with pytest.raises(ValueError):day_packets.packet_hash(bad)
 assert day_packets.packet_hash(p,verify_files=False)==original


def test_daily_seal_waits_until_0005_and_retains_empty_day_proof(gateway):
 c,app,s=gateway;s.material_protocol=True
 day=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat();now=int(time.time()*1000)
 assert not day_packets.seal(s,'!direct',day,3,day_packets.immutable_ms(day)-1)
 assert day_packets.seal(s,'!direct',day,3,now)
 packet=day_packets.build(s,'!direct',day,True);assert packet['entries']==[] and packet['complete']
 saved=day_packets.trusted(s,who(s),'!direct',day);assert saved['count']==0 and saved['sha256']==packet['sha256']
 data={'room':'!direct','start':day,'end':day,'days':[packet]}
 r=c.post('/api/packets/verify',json=data);assert r.status_code==200,r.text;assert r.json()['days'][0]['state']=='ok'
 wrong=copy.deepcopy(packet);wrong['entries']=text_packet(day)['entries'];wrong['entries'][0]['room_id']='!direct';raw=json.loads(wrong['entries'][0]['raw']);raw['room_id']='!direct';wrong['entries'][0]['raw']=archive.canonical(raw).decode()
 assert c.post('/api/packets/verify',json=data|{'days':[wrong]}).json()['days'][0]['state']=='mismatch'

@pytest.fixture
def publisher(gateway):
 c,app,s=gateway;s.material_protocol=True;sent=[]
 with connect(s.db) as db:db.execute("UPDATE room_modes SET encryption='plain' WHERE room='!direct'")
 async def call(method,path,token=None,data=None,params=None):
  from backend import server_messages,policy
  if path.endswith('/login') and '/admin/' in path:return {'access_token':'server-publisher'}
  if '/send/m.room.message/' in path:
   from urllib.parse import unquote
   room=unquote(path.split('/rooms/')[1].split('/send/')[0]);sender=who(s)['mxid']
   assert token=='server-publisher' and server_messages.granted(s,room,sender,'m.room.message',data)
   policy.validate_event(s,policy.room(s,room),'m.room.message',data,sender)
   sent.append(data);eid='$published'+str(len(sent));accepted(s,data,eid,at=int(time.time()*1000),seq=40+len(sent));return {'event_id':eid}
  return {}
 app.state.matrix.call=call
 return c,app,s,sent

def test_server_publisher_idempotency_and_payload_freeze(publisher,monkeypatch):
 c,app,s,sent=publisher;url='https://yitiao.org/games/parachute/share/'+'p'*32+'/'
 from backend import previews
 monkeypatch.setattr(previews,'metadata',lambda *_:{'type':'game','url':url,'title':'固定战绩','data':{'status':'jumped','exit_height':123.45}})
 data={'url':url,'txn_id':'once'}
 first=c.post('/api/messages/!direct/share',json=data);assert first.status_code==200,first.text
 second=c.post('/api/messages/!direct/share',json=data);assert second.status_code==200 and first.json()==second.json();assert len(sent)==1
 meta=materials.state(s,first.json()['event_id']);doc=json.loads((materials.directory(s)/meta['id']).read_text());assert doc['previews'][0]['data']['exit_height']==123.45
 assert c.post('/api/messages/!direct/share',json=data|{'url':url.replace('p'*32,'q'*32)}).status_code==409
 assert c.post('/api/messages/!direct/share',json={'content':sent[0]}).status_code==400

def test_media_is_server_published_with_same_file_id_and_original_hash(publisher):
 c,app,s,sent=publisher;body=b'one immutable file'
 uploaded=c.post('/api/files/!direct',content=body);assert uploaded.status_code==200,uploaded.text;f=uploaded.json()
 result=c.post('/api/messages/!direct/share',json={'content':{'msgtype':'m.file','body':'a.bin','url':f['url'],'org.yitiao.attachments':[f['id']]},'txn_id':'file-one'});assert result.status_code==200,result.text
 doc=json.loads((materials.directory(s)/sent[0][materials.KEY]['id']).read_text());assert doc['kind']=='media' and doc['files']==[{'id':f['id'],'bytes':str(len(body)),'sha256':hashlib.sha256(body).hexdigest()}]
 with connect(s.db) as db:assert db.execute('SELECT count(*) FROM files').fetchone()[0]==1

def test_frozen_gift_uses_original_card_after_transfer_and_destruction(cards,gateway):
 s,f,u,iid=cards;c,app,_=gateway;s.material_protocol=True
 from backend import assets,policy
 with connect(s.ledger) as db:db.execute('UPDATE yc_templates SET design=? WHERE tid=?',('{"name":"赠送时的原始数据","layers":[],"width_mm":85.6,"height_mm":54}','gift-fixture'))
 gift=assets.gift(s,u['alice'],policy.room(s,'!direct'),{'instance':iid,'recipient':'bob','key':'new-original-gift'})
 with connect(s.ledger) as db:db.execute("UPDATE yc_instances SET owner='carol' WHERE id=?",(iid,));db.execute('INSERT INTO yc_destroyed VALUES(?,?,?)',(iid,'carol',1))
 content=c.portal.call(materials.prepare,app,s,who(s),'!direct',{'msgtype':'m.text','body':'https://chat.yitiao.org/gifts/'+gift['id'],'org.yitiao.business':{'kind':'gift','id':gift['id']}})
 accepted(s,content)
 meta=materials.state(s,'$material');doc=json.loads((materials.directory(s)/meta['id']).read_text());assert doc['previews'][0]['data']['card']['design']['name']=='赠送时的原始数据'
 assert c.get('/api/materials/'+meta['id'],params={'event':'$material'}).status_code==200

def test_peer_day_relay_only_accepts_complete_verified_data_from_current_native_members(gateway):
 c,app,s=gateway;s.material_protocol=True
 day=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat();day_packets.seal(s,'!direct',day,3,int(time.time()*1000));packet=day_packets.build(s,'!direct',day,True)
 alice=who(s)|{'device':'a'};bob=who(s,'bob')|{'device':'b'}
 with connect(s.db) as db:
  for w in [alice,bob]:db.execute('INSERT INTO native_devices(proof_hash,subject,mxid,device,created,platform) VALUES(?,?,?,?,?,?)',(w['device'],w['subject'],w['mxid'],w['device'],time.time(),'android'))
 relay=day_packets.Relay(s);key=relay.ask(alice,'!direct',day)
 asked=relay.exchange(bob,{'requests':[]})['asked'];assert asked[0]['request']==key
 bad=copy.deepcopy(packet);bad['complete']=False
 with pytest.raises(HTTPException):relay.exchange(bob,{'offers':[{'request':key,'packet':bad}]})
 relay.exchange(bob,{'offers':[{'request':key,'packet':packet}]});assert relay.exchange(alice,{'requests':[key]})['fulfilled'][0]['packet']['sha256']==packet['sha256']
 with connect(s.ledger) as db:db.execute("UPDATE yichat_memberships SET membership='leave' WHERE room='!direct' AND mxid=?",(bob['mxid'],))
 assert relay.exchange(bob,{'requests':[]})['asked']==[]

def test_malformed_untrusted_packets_fail_as_whole_day_without_server_error(gateway):
 c,app,s=gateway;s.material_protocol=True;day=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat();day_packets.seal(s,'!direct',day,3,int(time.time()*1000));good=day_packets.build(s,'!direct',day,True)
 for entries in [[None],['bad'],[{'raw':'{broken'}],{'x':'bad'}]:
  packet=good|{'entries':entries};result=c.post('/api/packets/verify',json={'room':'!direct','start':day,'end':day,'days':[packet]});assert result.status_code==200,result.text;assert result.json()['days'][0]['state']=='mismatch'

def test_forward_clones_original_bytes_without_refetch_or_duplicate_storage(gateway,monkeypatch):
 c,app,s=gateway;s.material_protocol=True
 with connect(s.db) as db:db.execute("UPDATE room_modes SET encryption='plain' WHERE room='!direct'")
 from backend import previews
 url='https://yitiao.org/ycards/card/'+'c'*32+'/alice/'
 monkeypatch.setattr(previews,'metadata',lambda *_:{'type':'card','url':url,'title':'原始卡面','data':{'name':'转手后仍有原件','design':{'layers':[]}}})
 first=c.portal.call(materials.prepare,app,s,who(s),'!direct',{'msgtype':'m.text','body':url});accepted(s,first)
 monkeypatch.setattr(previews,'metadata',lambda *_:pytest.fail('Forward must never refetch a mutable source'))
 clone=c.portal.call(materials.prepare,app,s,who(s),'!direct',{'org.yitiao.material_source':'$material'})
 a=materials.directory(s)/first[materials.KEY]['id'];b=materials.directory(s)/clone[materials.KEY]['id']
 assert a.read_bytes()==b.read_bytes() and a.stat().st_ino==b.stat().st_ino
 assert first[materials.KEY]['id']!=clone[materials.KEY]['id']


def test_retention_classifies_only_legacy_special_urls_and_keeps_normal_files(gateway):
 from backend import protocol_retention
 c,app,s=gateway;s.material_protocol=True
 ordinary={'msgtype':'m.text','body':'普通文字'};old={'msgtype':'m.text','body':'https://yitiao.org/ycards/card/'+'c'*32+'/alice/'}
 assert materials.legacy_url(old)
 assert not materials.legacy_url(ordinary)
 assert not materials.legacy_url({'msgtype':'m.image','body':'photo.png','url':'mxc://chat.yitiao.org/'+'a'*48})
 assert not materials.legacy_url({'msgtype':'m.text','body':'photo','org.yitiao.forward_snapshot':{'content':{'msgtype':'m.image'},'previews':[]}})
 accepted(s,ordinary,'$normal',seq=40);accepted(s,old,'$old-special',seq=41)
 protocol_retention.initialize(s)
 with connect(s.db) as db:
  assert [r[0] for r in db.execute('SELECT event FROM legacy_material_cleanup')]==['$old-special']
  assert not protocol_retention.ready(s)
  db.execute("UPDATE protocol_expiry_jobs SET state='censored' WHERE event='$old-special'")
 assert protocol_retention.finish_entities(s)==1
 with archive.connect(s.archive_reader) as pg:
  assert pg.execute("SELECT 1 FROM events WHERE event_id='$normal'").fetchone()
  assert not pg.execute("SELECT 1 FROM events WHERE event_id='$old-special'").fetchone()

def test_open_api_material_read_scope_and_shared_download_budget(gateway,monkeypatch):
 c,app,s=gateway;s.material_protocol=True
 from test_open_api import new_key,api
 from backend import previews
 url='https://yitiao.org/games/parachute/share/'+'p'*32+'/'
 monkeypatch.setattr(previews,'metadata',lambda *_:{'type':'game','url':url,'title':'原件','data':{'status':'jumped'}})
 content=c.portal.call(materials.prepare,app,s,who(s),'!direct',{'msgtype':'m.text','body':url});accepted(s,content);key=new_key(c)
 client=api(app,key['key']);meta=materials.state(s,'$material')
 response=client.post('/open/v1/materials/check',json={'messages':[{'event_id':'$material'}]});assert response.status_code==200,response.text
 download=response.json()['messages'][0]['download'];first=client.get(download);assert first.status_code==200,first.text
 second=client.get(download);assert second.status_code==429,second.text
 assert client.post('/open/v1/materials/$material/confirm',json={'payload_sha256':meta['payload_sha256'],'sha256':meta['sha256']}).json()['state']=='ok'
 assert client.post('/open/v1/conversations/!direct/share',json={'url':url}).status_code==403

def test_daily_sealer_reuses_saved_hashes_and_matches_actual_uploaded_bytes(gateway,monkeypatch):
 c,app,s=gateway;s.material_protocol=True
 with connect(s.db) as db:db.execute("UPDATE room_modes SET encryption='plain' WHERE room='!direct'")
 binary=b'original attachment bytes for a complete GMT day';f=c.post('/api/files/!direct',content=binary).json()
 content=c.portal.call(materials.prepare,app,s,who(s),'!direct',{'msgtype':'m.file','body':'original.bin','url':f['url'],'org.yitiao.attachments':[f['id']]})
 day=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat();accepted(s,content,at=archive.day_bounds(day)[0]+600001)
 actual=day_packets.build(s,'!direct',day,True)
 monkeypatch.setattr(materials,'state',lambda *_:pytest.fail('The daily sealer must use saved material and file hashes'))
 saved=day_packets.build(s,'!direct',day,root_only=True)
 assert saved['sha256']==actual['sha256']
 assert day_packets.seal(s,'!direct',day,40,int(time.time()*1000))

def test_gift_commit_queue_gap_recovers_original_card_after_destruction(cards,publisher):
 s,_,users,iid=cards;c,app,_,sent=publisher
 from backend import assets,policy,server_messages
 result=assets.gift(s,users['alice'],policy.room(s,'!direct'),{'instance':iid,'recipient':'bob','key':'committed-before-queue'})
 with connect(s.ledger) as db:
  db.execute('INSERT INTO yc_destroyed VALUES(?,?,?)',(iid,'bob',time.time()))
 c.portal.call(server_messages.tick,app)
 assert len(sent)==1 and sent[0]['org.yitiao.business']=={'kind':'gift','id':result['id']}
 original=json.loads((materials.directory(s)/sent[0][materials.KEY]['id']).read_text())
 assert original['previews'][0]['data']['card']['id']==iid
 assert materials.state(s,'$published1')['finalized']
 c.portal.call(server_messages.tick,app);assert len(sent)==1

def test_record_snapshot_keeps_avatar_bytes_when_original_avatar_disappears(gateway):
 c,app,s=gateway;s.material_protocol=True
 from backend import sharing
 from PIL import Image
 target=s.site/'static/avatar/alice.jpg';target.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(32,32),'red').save(target)
 event={'event_id':'$avatar','sender':who(s)['mxid'],'room_id':'!direct','origin_server_ts':int(time.time()*1000),'content':{'msgtype':'m.text','body':'原始头像也属于记录数据'}}
 snapshot=sharing.snapshot(app,s,who(s),event);assert snapshot['avatar_data'].startswith('data:image/jpeg;base64,')
 data=base64.b64decode(snapshot['avatar_data'].split(',',1)[1]);assert data.startswith(b'\xff\xd8') and len(data)>50
 with connect(s.db) as db:db.execute("UPDATE room_modes SET encryption='plain' WHERE room='!direct'")
 accepted(s,{'msgtype':'m.text','body':'记录的原始头像'})
 result=c.post('/api/sharing/!direct/selection',json={'ids':['$material'],'mode':'image','identity':'both'});assert result.status_code==200,result.text
 target.unlink();avatar=c.get('/api/public-records/'+result.json()['hash']+'/avatars/0')
 assert avatar.status_code==200 and avatar.content==data and avatar.headers['content-type']=='image/jpeg'

def test_original_api_summaries_keep_receipt_gift_record_and_share_types(gateway):
 from backend.open_api import Describer
 c,app,s=gateway;s.material_protocol=True;describe=Describer(s,str)
 describe.order=lambda *_:{'principal':'100','title':'原始转账','kind':'direct','state':'paid','count':1}
 ref={'version':2,'id':'a'*48,'sha256':'b'*64,'kind':'share'}
 original={'msgtype':'m.text','body':'收款回执',materials.KEY:ref,'org.yitiao.material_type':'receipt','org.yitiao.business':{'kind':'order','id':'c'*32}}
 assert describe('!direct',original)['kind']=='receipt'
 gift=original|{'org.yitiao.material_type':'gift','org.yitiao.business':{'kind':'gift','id':'d'*32}};assert describe('!direct',gift)['kind']=='gift'
 record={'msgtype':'m.text','body':'聊天记录',materials.KEY:ref|{'kind':'record'}};assert describe('!direct',record)['share']['type']=='record'
 card={'msgtype':'m.text','body':'分享闪卡',materials.KEY:ref,'org.yitiao.material_type':'card'};assert describe('!direct',card)['share']['type']=='card'

def test_parallel_day_recovery_requests_remain_unique_and_bounded(gateway,monkeypatch):
 from concurrent.futures import ThreadPoolExecutor
 c,app,s=gateway;s.material_protocol=True
 day=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat();assert day_packets.seal(s,'!direct',day,3,int(time.time()*1000))
 devices=[who(s)|{'device':'parallel'+str(i)} for i in range(6)]
 with connect(s.db) as db:
  for w in devices:db.execute('INSERT INTO native_devices(proof_hash,subject,mxid,device,created,platform) VALUES(?,?,?,?,?,?)',(w['device'],w['subject'],w['mxid'],w['device'],time.time(),'android'))
 relay=day_packets.Relay(s);random=day_packets.secrets.token_urlsafe
 def slow(n):time.sleep(.002);return random(n)
 monkeypatch.setattr(day_packets.secrets,'token_urlsafe',slow)
 def ask(i):return relay.ask(devices[i%6],'!direct',day)
 with ThreadPoolExecutor(max_workers=6) as pool:keys=list(pool.map(ask,range(18)))
 assert len(relay.jobs)==6 and all(keys[i]==keys[i%6] for i in range(18))
 with ThreadPoolExecutor(max_workers=6) as pool:responses=list(pool.map(lambda w:relay.exchange(w,{'requests':[]}),devices))
 assert all(len(r['asked'])==5 for r in responses)
 for malformed in ([],{'offers':[{'request':[]}]}):
  with pytest.raises(HTTPException) as error:relay.exchange(devices[0],malformed)
  assert error.value.status_code==400
