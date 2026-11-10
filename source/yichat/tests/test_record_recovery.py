import base64,copy,hashlib,json,time
import pytest
from fastapi import HTTPException
from backend import archive,day_packets,public_records
from backend.db import connect
from test_material_protocol import accepted,who,text_packet
from test_gateway import gateway

def published(gateway,content=None):
 c,app,s=gateway
 accepted(s,content or {'msgtype':'m.text','body':'immutable chosen message'})
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$material'],'mode':'image'});assert r.status_code==200,r.text
 return r.json()
def native(s):
 w=who(s)|{'device':'RECOVERY'}
 with connect(s.db) as db:db.execute('INSERT INTO native_devices(proof_hash,subject,mxid,device,created,platform) VALUES(?,?,?,?,?,?)',('recovery',w['subject'],w['mxid'],w['device'],time.time(),'android'))
 return w

def test_public_capability_reconstructs_from_source_without_shared_payload(gateway):
 c,app,s=gateway;r=published(gateway);h=r['hash']
 with connect(s.db) as db:db.execute('DELETE FROM shared_records WHERE hash=?',(h,))
 c.cookies.clear();result=c.get('/api/public-records/'+h)
 assert result.status_code==200,result.text
 assert result.json()['canonical']==r['canonical']

def test_expired_record_only_relays_hash_verified_selected_messages_from_current_native_member(gateway):
 c,app,s=gateway;r=published(gateway);h=r['hash'];w=native(s)
 with connect(s.db) as db:
  db.execute('UPDATE record_publications SET expires=0 WHERE hash=?',(h,));public_records.cleanup(db)
 with archive.connect(s.archive_cleanup) as pg:pg.execute('DELETE FROM events WHERE event_id=%s',('$material',))
 c.cookies.clear();assert c.get('/api/public-records/'+h).status_code==202
 relay=app.state.record_recovery;asked=relay.exchange(w,{})['asked'];assert asked[0]['hash']==h and asked[0]['ids']==['$material']
 with pytest.raises(HTTPException):relay.exchange(w,{'offers':[{'hash':h,'canonical':r['canonical'].replace('immutable','tampered')}]})
 relay.exchange(w,{'offers':[{'hash':h,'canonical':r['canonical']}]})
 result=c.get('/api/public-records/'+h);assert result.status_code==200 and result.json()['canonical']==r['canonical']
 with connect(s.ledger) as db:db.execute("UPDATE yichat_memberships SET membership='leave' WHERE room='!direct' AND mxid=?",(w['mxid'],))
 with pytest.raises(HTTPException):relay.exchange(w,{'offers':[{'hash':h,'canonical':r['canonical']}]})
 assert c.get('/api/public-records/'+'0'*64).status_code==404

def test_record_file_relay_rejects_unselected_and_tampered_bytes(gateway):
 c,app,s=gateway;data=b'original photo';fid=c.post('/api/files/!direct',content=data).json()['id']
 r=published(gateway,{'msgtype':'m.image','body':'photo','url':'mxc://chat.yitiao.org/'+fid});h=r['hash'];w=native(s)
 with connect(s.db) as db:db.execute('UPDATE files SET removed=1 WHERE id=?',(fid,))
 c.cookies.clear();assert c.get('/api/public-records/'+h+'/files/'+fid+'/content').status_code==202
 relay=app.state.record_recovery
 with pytest.raises(HTTPException):relay.exchange(w,{'offers':[{'hash':h,'canonical':r['canonical'],'files':[{'id':fid,'data':base64.b64encode(b'bad').decode()}]}]})
 relay.exchange(w,{'offers':[{'hash':h,'canonical':r['canonical'],'files':[{'id':fid,'data':base64.b64encode(data).decode()}]}]})
 assert c.get('/api/public-records/'+h+'/files/'+fid+'/content').content==data
 assert c.get('/api/public-records/'+h+'/files/'+'a'*48+'/content').status_code==404

def test_undownloaded_file_uses_saved_hash_but_supplied_bytes_must_verify():
 p=text_packet();data=b'download only on demand';f={'id':'f'*48,'bytes':str(len(data)),'sha256':hashlib.sha256(data).hexdigest()};p['entries'][0]['files']=[f]
 original=day_packets.packet_hash(p);f['data']=base64.b64encode(data).decode();assert day_packets.packet_hash(p)==original
 f['data']=base64.b64encode(b'corrupt').decode()
 with pytest.raises(ValueError):day_packets.packet_hash(p)

def test_record_material_envelope_preserves_access_pointer_for_missing_original(gateway):
 from backend import materials
 c,app,s=gateway;s.material_protocol=True
 accepted(s,{'msgtype':'m.text','body':'selected original'})
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$material'],'mode':'combined','target':'!group'});assert r.status_code==200,r.text
 value=r.json();prepared=c.portal.call(materials.prepare,app,s,who(s),'!group',value['content'])
 assert prepared['org.yitiao.record_access']==value['hash']
 assert prepared[materials.KEY]['kind']=='record'
