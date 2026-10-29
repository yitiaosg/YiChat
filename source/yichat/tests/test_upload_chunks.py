import hashlib,secrets,time
from unittest.mock import patch
import pytest
from backend import archive,uploads
from backend.db import connect
from test_gateway import gateway


def start(c,size=300000,key=None):
    r=c.post('/api/uploads',json={'room':'!direct','bytes':size,'key':key or secrets.token_hex(16)});assert r.status_code==200,r.text;return r.json()

def put(c,uid,offset,body):return c.put('/api/uploads/'+uid,content=body,headers={'X-Upload-Offset':str(offset),'Content-Type':'application/octet-stream'})

def finish(c,uid):return c.post('/api/uploads/'+uid+'/complete',json={})


def test_chunks_ack_bytes_and_keep_original_hash_and_download_permissions(gateway):
    c,app,s=gateway;body=b'raw-file-bytes'*22000;o=start(c,len(body));uid=o['id']
    for offset in range(0,len(body),uploads.CHUNK_BYTES):
        r=put(c,uid,offset,body[offset:offset+uploads.CHUNK_BYTES]);assert r.status_code==200,r.text
        assert r.json()['offset']==min(len(body),offset+uploads.CHUNK_BYTES)
        with connect(s.db) as db:assert not db.execute('SELECT 1 FROM files WHERE id=?',(uid,)).fetchone()
    r=finish(c,uid);assert r.status_code==200,r.text
    assert r.json()['state']=='complete' and r.json()['bytes']==len(body)
    assert finish(c,uid).json()['id']==uid
    assert c.get('/api/files/'+uid+'/content').content==body
    with connect(s.db) as db:assert db.execute('SELECT sha256 FROM file_hashes WHERE id=?',(uid,)).fetchone()[0]==hashlib.sha256(body).hexdigest()
    with archive.connect(s.archive_reader) as pg:assert pg.execute('SELECT sha256 FROM attachments WHERE id=%s',(uid,)).fetchone()['sha256']==hashlib.sha256(body).hexdigest()


def test_lost_start_or_chunk_ack_does_not_duplicate_file_or_reset_progress(gateway):
    c,app,s=gateway;key=secrets.token_hex(16);o=start(c,200000,key);uid=o['id']
    assert start(c,200000,key)['id']==uid
    data=b'a'*uploads.CHUNK_BYTES
    assert put(c,uid,0,data).json()['offset']==len(data)
    assert c.get('/api/uploads/'+uid).json()['offset']==len(data)
    assert put(c,uid,0,data).json()['offset']==len(data)
    assert put(c,uid,0,b'b'*len(data)).status_code==409
    assert put(c,uid,len(data),b'c'*(200000-len(data))).status_code==200
    assert finish(c,uid).status_code==200
    with connect(s.db) as db:
        assert db.execute('SELECT count(*) FROM files').fetchone()[0]==1
        assert db.execute('SELECT bytes FROM upload_usage').fetchone()[0]==200000+len(data)


def test_partial_uncommitted_append_is_discarded_on_retry(gateway):
    c,app,s=gateway;uid=start(c,8)['id'];part=s.objects/(uid+'.part');part.write_bytes(b'uncommitted-junk')
    assert put(c,uid,0,b'correct!').json()['offset']==8
    assert part.read_bytes()==b'correct!'
    assert finish(c,uid).status_code==200


def test_pg_confirmation_failure_is_recoverable_without_exposing_unconfirmed_file(gateway):
    c,app,s=gateway;uid=start(c,4)['id'];assert put(c,uid,0,b'data').status_code==200
    with patch.object(archive,'record_attachment',side_effect=RuntimeError('isolated fault')):
        assert finish(c,uid).status_code==503
    with connect(s.db) as db:
        assert db.execute('SELECT state FROM file_uploads WHERE id=?',(uid,)).fetchone()[0]=='finalizing'
        assert not db.execute('SELECT 1 FROM files WHERE id=?',(uid,)).fetchone()
    assert finish(c,uid).json()['state']=='complete'
    assert c.get('/api/files/'+uid+'/content').content==b'data'


def test_cancel_draft_and_peer_cannot_read_or_append(gateway):
    c,app,s=gateway;uid=start(c,8)['id']
    c.cookies.clear();c.headers['Authorization']='Bearer bob-token'
    assert c.get('/api/uploads/'+uid).status_code==404
    assert put(c,uid,0,b'12345678').status_code==404
    c.headers['Authorization']='Bearer session-token'
    assert c.delete('/api/uploads/'+uid).status_code==200
    assert not (s.objects/(uid+'.part')).exists()
    assert c.get('/api/uploads/'+uid).status_code==410
    with connect(s.db) as db:assert db.execute('SELECT count(*) FROM upload_leases').fetchone()[0]==0


def test_incomplete_and_oversize_chunks_do_not_publish_or_overwrite(gateway):
    c,app,s=gateway;uid=start(c,8)['id']
    assert finish(c,uid).status_code==409
    assert put(c,uid,0,b'x'*(uploads.CHUNK_BYTES+1)).status_code==413
    assert put(c,uid,0,b'x'*9).status_code==413
    assert put(c,uid,0,b'1234').json()['offset']==4
    assert put(c,uid,6,b'56').status_code==409
    with connect(s.db) as db:assert not db.execute('SELECT 1 FROM files').fetchone()


def test_expired_draft_is_cancelled_but_finalizing_file_is_reconciled(gateway):
    c,app,s=gateway;draft=start(c,8)['id'];ready=start(c,4)['id']
    assert put(c,ready,0,b'data').status_code==200
    with patch.object(archive,'record_attachment',side_effect=RuntimeError('isolated fault')):
        assert finish(c,ready).status_code==503
    with connect(s.db) as db:db.execute('UPDATE file_uploads SET expires=?',(time.time()-1,))
    uploads.reconcile(s)
    assert c.get('/api/uploads/'+draft).status_code==410
    assert not (s.objects/(draft+'.part')).exists()
    assert c.get('/api/uploads/'+ready).json()['state']=='complete'
    assert c.get('/api/files/'+ready+'/content').content==b'data'
