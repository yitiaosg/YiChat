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
 forwarded=r.json()['messages'][0];policy.validate_event(s,policy.room(s,'!group'),'m.room.message',forwarded,'@alice:chat.yitiao.org')
 with pytest.raises(HTTPException):policy.validate_event(s,policy.room(s,'!group'),'m.room.message',forwarded|{'body':'forged'},'@alice:chat.yitiao.org')

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
