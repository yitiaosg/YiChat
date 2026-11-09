import json,time,hashlib
import pytest
from backend import archive,history,policy
from backend.db import connect
from test_gateway import gateway
from fastapi import HTTPException

def seed(s,content=None,age=600001):
 now=int(time.time()*1000);raw=json.dumps({'type':'m.room.message','sender':'@alice:chat.yitiao.org','origin_server_ts':now-age,'content':content or {'msgtype':'m.text','body':'old message'}});rows=[('$selected','!direct',40,now-age,raw)];archive.ingest_batch(s.archive_ingest,rows,40,now);history.record_rows(s,rows)

def test_age_deleted_redacted_and_nested_records_are_denied(gateway):
 c,app,s=gateway;seed(s,age=290000)
 def select():return c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image'})
 assert select().status_code==409
 with connect(s.db) as db:db.execute('DELETE FROM history_receipts WHERE event_id=?',('$selected',))
 assert c.post('/api/sharing/!direct/selection',json={'ids':['$deleted'],'mode':'image'}).status_code==410

def test_snapshot_hash_sharing_policy_and_signature(gateway):
 c,app,s=gateway;seed(s)
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'combined','target':'!group'});assert r.status_code==200,r.text
 result=r.json();content=result['content'];assert hashlib.sha256(content['org.yitiao.record_json'].encode()).hexdigest()==result['hash']
 assert c.get('/api/records/'+result['hash']+'?room=!group').status_code==200
 policy.validate_event(s,policy.room(s,'!group'),'m.room.message',content,'@alice:chat.yitiao.org')
 with pytest.raises(HTTPException):policy.validate_event(s,policy.room(s,'!group'),'m.room.message',content|{'org.yitiao.record_json':'{}'},'@alice:chat.yitiao.org')
 assert c.put('/api/sharing/!direct',json={'blocked':True}).status_code==200
 assert c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'combined','target':'!group'}).status_code==403
 assert c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image'}).status_code==403
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'individual','target':'!group'});assert r.status_code==200,r.text
 forwarded=r.json()['messages'][0];assert 'org.yitiao.forward_snapshot' not in forwarded;assert 'org.yitiao.forward_proof' not in forwarded;policy.validate_event(s,policy.room(s,'!group'),'m.room.message',forwarded,'@alice:chat.yitiao.org')
 assert forwarded['body']=='old message'  # Same admission rules as a newly authored message.

def test_file_reuse_keeps_id_and_grants_only_intended_sender(gateway):
 c,app,s=gateway
 uploaded=c.post('/api/files/!direct',content=b'same photo').json();fid=uploaded['id'];h=hashlib.sha256(b'same photo').hexdigest()
 r=c.post('/api/files/!group/reuse',json={'sha256':h});assert r.status_code==200,r.text;assert r.json()['id']==fid
 with connect(s.db) as db:assert db.execute('SELECT count(*) FROM files').fetchone()[0]==1
 policy.admit_attachments(s,'$reused','!group','@alice:chat.yitiao.org','m.room.message',{'org.yitiao.attachments':[fid]},'test','boot')
 with pytest.raises(HTTPException):policy.admit_attachments(s,'$bad','!group','@bob:chat.yitiao.org','m.room.message',{'org.yitiao.attachments':[fid]},'test','boot')


def test_identical_verified_uploads_share_physical_bytes(gateway):
 c,app,s=gateway
 a=c.post('/api/files/!direct',content=b'verified identical bytes').json()
 b=c.post('/api/files/!group',content=b'verified identical bytes').json()
 assert a['id']!=b['id']
 import os
 assert os.path.samefile(s.objects/a['id'],s.objects/b['id'])
 assert c.get('/api/files/'+a['id']+'/content').content==b'verified identical bytes'


def test_record_snapshots_include_nickname_and_avatar(gateway):
 c,app,s=gateway
 with connect(s.site/'account.db') as db:db.execute('UPDATE users SET nickname=? WHERE username=?',('爱丽丝','alice'))
 avatars=s.site/'static/avatar';avatars.mkdir(parents=True);(avatars/'alice.jpg').write_bytes(b'avatar bytes')
 seed(s)
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'combined','target':'!group'});assert r.status_code==200,r.text
 content=r.json()['content'];payload=json.loads(content['org.yitiao.record_json']);item=payload['messages'][0]
 assert item['username']=='alice' and item['name']=='爱丽丝'
 assert item['avatar_url'].startswith('/api/avatars/alice?v=')
 assert hashlib.sha256(content['org.yitiao.record_json'].encode()).hexdigest()==content['org.yitiao.record']


def test_image_and_combined_share_public_hash_and_identity(gateway):
 c,app,s=gateway;seed(s)
 prepared=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'prepare','identity':'avatar'});assert prepared.status_code==200,prepared.text
 with connect(s.db) as db:assert db.execute('SELECT count(*) FROM record_publications').fetchone()[0]==0
 image=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image','identity':'avatar'}).json()
 combined=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'combined','identity':'avatar','target':'!group'}).json()
 assert 'hash' not in prepared.json() and 'url' not in prepared.json()
 assert image['hash']==combined['hash']
 assert image['url']==combined['url'] and image['payload']['identity']=='avatar'
 assert image['payload']['owner']=='@alice:chat.yitiao.org'
 c.cookies.clear();c.headers.pop('Origin',None)
 response=c.get('/api/public-records/'+image['hash']);assert response.status_code==200,response.text
 assert hashlib.sha256(response.json()['canonical'].encode()).hexdigest()==image['hash']
 # An unrelated/stale login must not block this explicitly public route.
 assert c.get('/api/public-records/'+image['hash'],headers={'Authorization':'Bearer expired'}).status_code==200

@pytest.mark.parametrize('which',['orders','gifts'])
def test_business_messages_cannot_be_forwarded_individually(gateway,which):
 c,app,s=gateway;seed(s,{'msgtype':'m.text','body':'https://chat.yitiao.org/'+which+'/'+'a'*32})
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'individual','target':'!group'});assert r.status_code==400,r.text


def test_record_can_only_be_forwarded_as_ordinary_original_link(gateway):
 c,app,s=gateway;body='https://chat.yitiao.org/records/'+'a'*64
 seed(s,{'msgtype':'m.text','body':body,'org.yitiao.record':'a'*64,'org.yitiao.record_json':'private original payload'})
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'combined','target':'!group'});assert r.status_code==400
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'individual','target':'!group'});assert r.status_code==200,r.text
 assert r.json()['messages']==[{'msgtype':'m.text','body':body,'org.yitiao.attachments':[]}]


def test_public_record_limits_files_and_ends_when_source_deleted(gateway,postgres):
 c,app,s=gateway
 own=c.post('/api/files/!direct',content=b'public photo').json()['id'];other=c.post('/api/files/!direct',content=b'private unrelated').json()['id']
 seed(s,{'msgtype':'m.image','body':'photo.png','url':'mxc://chat.yitiao.org/'+own,'info':{'mimetype':'image/png'}})
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image'});assert r.status_code==200,r.text;h=r.json()['hash']
 c.cookies.clear();c.headers.pop('Origin',None)
 assert c.get('/api/public-records/'+h+'/files/'+own+'/content').content==b'public photo'
 assert c.get('/api/public-records/'+h+'/files/'+other+'/content').status_code==404
 assert c.get('/api/files/'+own+'/content').status_code==401
 with archive.connect(postgres['owner']) as pg:pg.execute('DELETE FROM events WHERE event_id=%s',('$selected',))
 assert c.get('/api/public-records/'+h).status_code==410
 assert c.get('/api/public-records/'+h+'/files/'+own+'/content').status_code==410


def test_public_record_expires_by_original_receipt_not_link_creation(gateway,postgres):
 c,app,s=gateway
 with archive.connect(postgres['owner']) as pg:pg.execute('UPDATE memberships SET received_ms=%s',(int(time.time()*1000)-10*86400000,))
 seed(s,age=6*86400000)
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image'});assert r.status_code==200,r.text
 assert 86300<r.json()['expires']-time.time()<86500
 with connect(s.db) as db:db.execute('UPDATE record_publications SET expires=?',(time.time()-1,))
 c.cookies.clear();assert c.get('/api/public-records/'+r.json()['hash']).status_code==410


def test_public_record_revoked_with_source_and_old_messages_cannot_be_published(gateway,postgres):
 c,app,s=gateway
 with archive.connect(postgres['owner']) as pg:pg.execute('UPDATE memberships SET received_ms=%s',(int(time.time()*1000)-10*86400000,))
 seed(s,age=8*86400000)
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image'});assert r.status_code==410,r.text
 # Individual forwarding still follows the ordinary server retention window.
 assert c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'individual','target':'!group'}).status_code==200


def test_public_record_is_revoked_when_original_is_redacted(gateway):
 c,app,s=gateway;seed(s)
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image'});assert r.status_code==200,r.text
 with connect(s.db) as db:db.execute('INSERT INTO history_receipts VALUES(?,?,?,?,?,?,?,?,?,?)',('$redact','!direct',41,int(time.time()*1000),'@alice:chat.yitiao.org','m.room.redaction',int(time.time()*1000),'a'*64,'$selected',None))
 c.cookies.clear();assert c.get('/api/public-records/'+r.json()['hash']).status_code==410


def test_existing_record_hash_is_preserved_for_public_urls(gateway):
 c,app,s=gateway;seed(s)
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'combined','target':'!group'}).json()
 with connect(s.db) as db:db.execute('DELETE FROM record_publications')
 c.cookies.clear();public=c.get('/api/public-records/'+r['hash']);assert public.status_code==200,public.text
 assert public.json()['canonical']==r['canonical']


def test_legacy_business_forward_keeps_static_preview_in_record(gateway):
 c,app,s=gateway
 content={'msgtype':'m.text','body':'https://chat.yitiao.org/orders/'+'b'*32}
 frozen={'content':content,'previews':[{'type':'order','url':content['body'],'data':{'kind':'group','amount':'12.5','title':'红包','state':'paid','actions':{'claim':True},'authorize_url':'must disappear'}}]}
 seed(s,content|{'org.yitiao.forward_snapshot':frozen})
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image'});assert r.status_code==200,r.text
 meta=r.json()['messages'][0]['previews'][0];assert meta['data']['amount']=='12.5' and meta['data']['actions']=={} and meta['data']['authorize_url'] is None


def test_disabled_preview_stays_plain_text_in_shared_record(gateway,monkeypatch):
 from backend import previews
 c,app,s=gateway
 seed(s,{'msgtype':'m.text','body':'https://yidt.yitiao.org/explorer/tx/'+'a'*64,'org.yitiao.no_preview':True})
 def unexpected(*args):raise AssertionError('Disabled preview must not be expanded')
 monkeypatch.setattr(previews,'metadata',unexpected)
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'image'});assert r.status_code==200,r.text
 assert r.json()['messages'][0]['previews']==[]


def test_known_unreadable_file_id_cannot_be_published_or_forwarded(gateway):
 c,app,s=gateway;c.cookies.set('yc_session','bob-token')
 r=c.post('/api/files/!direct',content=b'bob private unreferenced');assert r.status_code==200,r.text
 fid=r.json()['id'];c.cookies.set('yc_session','session-token')
 seed(s,{'msgtype':'m.image','body':'guessed.png','url':'mxc://chat.yitiao.org/'+fid,'info':{'mimetype':'image/png'}})
 for mode in ['image','individual']:
  r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':mode,'target':'!group'});assert r.status_code==403,r.text
 with connect(s.db) as db:assert db.execute('SELECT count(*) FROM record_publications').fetchone()[0]==0


def test_archive_accepts_original_file_only_with_server_reuse_grant(gateway):
 c,app,s=gateway
 fid=c.post('/api/files/!direct',content=b'reused original').json()['id']
 now=int(time.time()*1000);raw=json.dumps({'type':'m.room.message','sender':'@alice:chat.yitiao.org','content':{'body':'forwarded','org.yitiao.attachments':[fid]}})
 rows=[('$cross-room','!group',100,now,raw)]
 with pytest.raises(RuntimeError,match='authenticated upload'):archive.ingest_batch(s.archive_ingest,rows,100,now)
 archive.ingest_batch(s.archive_ingest,rows,100,now,reuse=[(fid,'!group','@alice:chat.yitiao.org',time.time()+60)])
 with archive.connect(s.archive_reader) as pg:assert pg.execute('SELECT 1 FROM attachment_references WHERE file=%s AND room=%s',(fid,'!group')).fetchone()


def test_preview_creates_no_hash_and_combined_url_waits_for_accepted_message(gateway):
 from backend import public_records
 c,app,s=gateway;seed(s)
 preview=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'prepare'}).json()
 assert 'hash' not in preview and 'url' not in preview
 with connect(s.db) as db:assert db.execute('SELECT count(*) FROM shared_records').fetchone()[0]==0
 result=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'combined','target':'!group'}).json()
 assert result['active'] is False
 assert c.get('/api/public-records/'+result['hash']).status_code==404
 public_records.activate_message(s,'!group',result['content'])
 assert c.get('/api/public-records/'+result['hash']).status_code==200

def test_order_url_in_body_and_share_field_produces_one_snapshot(gateway):
 from test_open_api import order
 c,app,s=gateway;oid='a'*32;url='https://chat.yitiao.org/orders/'+oid
 order(s,app,oid,'!direct','direct','1.25')
 with connect(s.db) as db:ids={r['username']:r['subject'] for r in db.execute('SELECT username,subject FROM identities')}
 with connect(s.ledger) as db:
  db.execute('INSERT INTO yichat_order_participants(order_id,sender_uid,recipient_uid,request_key) VALUES(?,?,?,?)',(oid,ids['alice'],ids['bob'],'duplicate-order-preview'))
  db.execute('UPDATE yichat_orders SET expires=? WHERE id=?',(time.time()+3600,oid))
 seed(s,{'msgtype':'m.text','body':url,'org.yitiao.share':{'type':'order','url':url}})
 r=c.post('/api/sharing/!direct/selection',json={'ids':['$selected'],'mode':'prepare'});assert r.status_code==200,r.text
 previews=r.json()['messages'][0]['previews'];assert len(previews)==1 and previews[0]['type']=='order'
