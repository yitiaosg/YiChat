import copy
import io
import json
import zipfile
import concurrent.futures
from datetime import date
import pytest
import psycopg
from backend import archive
from backend.archive_worker import cleanup_entities

DAY=date(2026,1,2)
START,END=archive.day_bounds(DAY)


def batch(s,events,fence=True):
    archive.ingest_batch(s.archive_ingest,events,max(r[2] for r in events),END+1000,[(DAY,max(r[2] for r in events),END+1000)] if fence else [])


def event(seq,received,kind='m.room.encrypted',content=None,state_key=None,room='!room'):
    data={'type':kind,'sender':'@alice:chat.yitiao.org','content':content or {'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'CIPHER-'+str(seq)},'origin_server_ts':1,'depth':seq,'unsigned':{'age':123}}
    if state_key is not None:data['state_key']=state_key
    return ('$'+str(seq),room,seq,received,json.dumps(data))


def seeded(s,attachments=None):
    rows=[event(1,START-1000,'m.room.member',{'membership':'join'},'@alice:chat.yitiao.org'),
          event(2,START+5,content={'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'CIPHER-2',archive.ATTACHMENT_KEY:attachments} if attachments else None),event(3,END-1,'m.room.redaction',{'redacts':'$2'})]
    batch(s,rows)
    return rows


def test_deterministic_ciphertext_archive_independent_downloads_and_specs(env):
    s,f,u=env
    data=b'actual encrypted attachment';(s.objects/'abc').write_bytes(data)
    archive.record_attachment(s.archive_ingest,'abc','!room','@alice:chat.yitiao.org',START+20,len(data),archive.sha256(data))
    rows=seeded(s,['abc'])
    a=archive.seal_day(s,'!room',DAY,'events-manifest',END+1)
    b=archive.seal_day(s,'!room',DAY,'with-attachments',END+1)
    assert a['sha256']!=b['sha256'];assert a['event_count']==2
    archive.authorize(s.archive_reader,'!room','@alice:chat.yitiao.org',DAY)
    path=archive.artifact_path(s.archive_path,'!room',DAY,'with-attachments')
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:downloads=list(pool.map(lambda _:path.read_bytes(),range(12)))
    assert len(set(downloads))==1
    with zipfile.ZipFile(io.BytesIO(downloads[0])) as z:
        assert z.namelist()==sorted(z.namelist())
        assert z.read('attachments/abc')==data
        assert b'CIPHER-2' in z.read('events.ndjson')
        assert b'age' not in z.read('events.ndjson')
        assert all(x.date_time==(1980,1,1,0,0,0) for x in z.infolist())
    # Rebuild into a different storage root, using the same source/spec, not a
    # cached server download; sealed_at and downloader never enter these bytes.
    with archive.connect(s.archive_reader) as c:_,files,entries,_=archive.contents(c,'!room',DAY,'with-attachments')
    rebuilt=s.root/'rebuilt.zip';archive.write_zip(rebuilt,entries,files,s.objects,True)
    assert rebuilt.read_bytes()==downloads[0]


def test_utc_boundary_edit_redaction_and_retry_dedupe(env):
    s,f,u=env
    rows=[event(1,START),event(2,END-1,'m.room.message',{'body':'hello','m.new_content':{'body':'edit'},'m.relates_to':{'rel_type':'m.replace','event_id':'$1'}}),event(3,END,'m.room.redaction',{'redacts':'$1'})]
    batch(s,rows);batch(s,rows)
    a=archive.seal_day(s,'!room',DAY,'events-manifest',END+10)
    assert a['event_count']==2 and a['last_sequence']==2
    with archive.connect(s.archive_reader) as c:
        assert c.execute('SELECT day FROM events WHERE event_id=%s',('$3',)).fetchone()['day']==date(2026,1,3)


def test_pending_stream_fence_missing_files_and_current_day_never_seal(env):
    s,f,u=env
    batch(s,[event(1,START)],fence=False)
    with pytest.raises(RuntimeError):archive.seal_day(s,'!room',DAY,'events-manifest',END+1)
    with pytest.raises(ValueError):archive.seal_day(s,'!room',DAY,'events-manifest',END-1)
    archive.ingest_batch(s.archive_ingest,[],1,END+2000,[(DAY,2,END+1000)])
    with pytest.raises(RuntimeError):archive.seal_day(s,'!room',DAY,'events-manifest',END+2000)
    archive.record_attachment(s.archive_ingest,'missing','!room','@alice:chat.yitiao.org',START+100,3,'a'*64)
    archive.ingest_batch(s.archive_ingest,[event(2,END-10,content={archive.ATTACHMENT_KEY:['missing']})],2,END+3000)
    with pytest.raises(FileNotFoundError):archive.seal_day(s,'!room',DAY,'with-attachments',END+3000)
    assert archive.summary(s.archive_reader,'!room',DAY,'with-attachments') is None


@pytest.mark.parametrize('stage',['before_rename','before_commit'])
def test_crash_before_metadata_commit_is_recoverable(env,stage):
    s,f,u=env;seeded(s)
    def crash(at):
        if at==stage:raise RuntimeError('simulated process crash')
    with pytest.raises(RuntimeError):archive.seal_day(s,'!room',DAY,'events-manifest',END+1,crash)
    assert archive.summary(s.archive_reader,'!room',DAY,'events-manifest') is None
    actual=archive.seal_day(s,'!room',DAY,'events-manifest',END+2)
    assert actual['sha256']==archive.file_digest(archive.artifact_path(s.archive_path,'!room',DAY,'events-manifest'))[0]


def test_partial_day_join_leave_rejoin_denied(env):
    s,f,u=env
    rows=[event(1,START-1,'m.room.member',{'membership':'join'},'@alice:chat.yitiao.org'),
          event(2,START+10,'m.room.member',{'membership':'join'},'@bob:chat.yitiao.org'),
          event(3,START+20,'m.room.member',{'membership':'leave'},'@alice:chat.yitiao.org'),
          event(4,START+30,'m.room.member',{'membership':'join'},'@alice:chat.yitiao.org')]
    batch(s,rows)
    for name in ('alice','bob','eve'):
        with pytest.raises(PermissionError):archive.authorize(s.archive_reader,'!room','@'+name+':chat.yitiao.org',DAY)


def test_evidence_failure_stops_cleanup_and_role_isolation(env,postgres):
    s,f,u=env;seeded(s)
    archive.seal_day(s,'!room',DAY,'events-manifest',END+1)
    with pytest.raises(RuntimeError):archive.eligible_cleanup(s,'!room',DAY,END+31*86400000)
    archive.seal_day(s,'!room',DAY,'with-attachments',END+1)
    with archive.connect(s.archive_reader) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute("UPDATE archives SET sha256=%s",('0'*64,))
    with archive.connect(s.archive_ingest) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):c.execute('DELETE FROM archives')
    with archive.connect(postgres['owner']) as c:
        with pytest.raises(psycopg.errors.RaiseException):c.execute('UPDATE archives SET bytes=0')
    archive.record_failure(s.archive_ingest)
    with pytest.raises(RuntimeError):archive.eligible_cleanup(s,'!room',DAY,END+31*86400000)
    assert archive.artifact_path(s.archive_path,'!room',DAY,'events-manifest').exists()


def test_cleanup_preserves_authenticated_digest_and_permissions(env,postgres):
    s,f,u=env;seeded(s)
    summaries=[archive.seal_day(s,'!room',DAY,spec,END+1) for spec in archive.SPECS]
    archive.ingest_batch(s.archive_ingest,[],3,END+30*86400000-1)
    assert not archive.eligible_cleanup(s,'!room',DAY,END+30*86400000-1)
    assert archive.eligible_cleanup(s,'!room',DAY,END+30*86400000)
    with archive.connect(postgres['owner']) as c:c.execute('UPDATE archive_audit SET history_done=true')
    archive.ingest_batch(s.archive_ingest,[],3,END+31*86400000)
    assert cleanup_entities(s,'!room',DAY,END+31*86400000)
    for old in summaries:
        new=archive.summary(s.archive_reader,'!room',DAY,old['spec'])
        assert new['sha256']==old['sha256'] and new['cleaned_at'] is not None
        assert not archive.artifact_path(s.archive_path,'!room',DAY,old['spec']).exists()
    archive.authorize(s.archive_reader,'!room','@alice:chat.yitiao.org',DAY)


def test_upload_pending_receipt_blocks_midnight_seal(env,postgres):
    s,f,u=env;seeded(s)
    with archive.connect(s.archive_ingest) as c:c.execute('INSERT INTO upload_reservations VALUES(%s,%s)',('pending',END-1))
    with pytest.raises(RuntimeError):archive.seal_day(s,'!room',DAY,'events-manifest',END+1)
    archive.record_attachment(s.archive_ingest,'pending','!room','alice',END-1,0,archive.sha256(b''))
    assert archive.seal_day(s,'!room',DAY,'events-manifest',END+1)['event_count']==2


def test_cleanup_crash_after_unlink_resumes_without_losing_evidence(env,postgres):
    s,f,u=env;seeded(s)
    before=[archive.seal_day(s,'!room',DAY,spec,END+1) for spec in archive.SPECS]
    with archive.connect(postgres['owner']) as c:c.execute('UPDATE archive_audit SET history_done=true')
    archive.ingest_batch(s.archive_ingest,[],3,END+31*86400000)
    def crash():raise RuntimeError('process terminated after unlink')
    with pytest.raises(RuntimeError):cleanup_entities(s,'!room',DAY,END+31*86400000,crash)
    archive.ingest_batch(s.archive_ingest,[],3,END+31*86400000)
    assert cleanup_entities(s,'!room',DAY,END+31*86400000)
    assert archive.summary(s.archive_reader,'!room',DAY,'events-manifest')['sha256']==before[0]['sha256']


def test_late_accepted_event_quarantines_complete_date_and_blocks_cleanup(env):
    s,f,u=env;seeded(s)
    original=archive.seal_day(s,'!room',DAY,'events-manifest',END+1)
    with pytest.raises(archive.LateSealedEvent):archive.ingest_batch(s.archive_ingest,[event(4,END-10)],4,END+2000)
    recorded=archive.summary(s.archive_reader,'!room',DAY,'events-manifest')
    assert recorded['has_gaps'] and recorded['sha256']==original['sha256']
    with pytest.raises(RuntimeError):archive.eligible_cleanup(s,'!room',DAY,END+31*86400000)


def test_restart_can_reuse_unused_tail_allocations_but_not_accepted_positions(env):
    s,f,u=env;seeded(s)
    archive.ingest_batch(s.archive_ingest,[],100,END+2000,[(date(2026,1,3),100,END+2000)])
    assert archive.reconcile_source_position(s.archive_ingest,3)==3
    with archive.connect(s.archive_reader) as c:
        assert c.execute('SELECT sequence FROM fences WHERE day=%s',(date(2026,1,3),)).fetchone()['sequence']==3
    archive.ingest_batch(s.archive_ingest,[event(4,END+10)],4,END+3000)
    assert archive.reconcile_source_position(s.archive_ingest,4)==4
    with pytest.raises(RuntimeError):archive.reconcile_source_position(s.archive_ingest,2)


def test_empty_utc_dates_are_discovered_and_sealed_deterministically(env):
    s,f,u=env;seeded(s)
    empty=date(2026,1,4);_,end=archive.day_bounds(empty)
    archive.ingest_batch(s.archive_ingest,[],3,end+1,[(empty,3,end+1)])
    assert any(r['day']==empty for r in archive.pending_days(s))
    metadata=archive.seal_day(s,'!room',empty,'events-manifest',end+1)
    assert metadata['event_count']==0 and metadata['first_sequence'] is None and metadata['last_sequence'] is None


def test_cross_day_attachment_references_use_accepted_event_day_and_drafts_are_private(env):
    s,f,u=env
    data=b'actual uploaded ciphertext';(s.objects/'cross').write_bytes(data)
    archive.record_attachment(s.archive_ingest,'cross','!room','@alice:chat.yitiao.org',START-1,len(data),archive.sha256(data))
    archive.record_attachment(s.archive_ingest,'draft','!room','@alice:chat.yitiao.org',START,1,archive.sha256(b'x'))
    seeded(s,['cross'])
    result=archive.seal_day(s,'!room',DAY,'with-attachments',END+1)
    with zipfile.ZipFile(archive.artifact_path(s.archive_path,'!room',DAY,'with-attachments')) as z:
        manifest=json.loads(z.read('attachments.json'))
        assert [r['id'] for r in manifest['attachments']]==['cross']
        assert manifest['attachments'][0]['event_ids']==['$2']
        assert z.read('attachments/cross')==data
        assert 'attachments/draft' not in z.namelist()


def test_earlier_day_cleanup_preserves_blob_needed_by_later_reference(env,postgres):
    from backend.db import connect
    s,f,u=env;data=b'shared ciphertext';fid='b'*48
    (s.objects/fid).write_bytes(data)
    archive.record_attachment(s.archive_ingest,fid,'!room','@alice:chat.yitiao.org',START,len(data),archive.sha256(data))
    with connect(s.db) as local:
        local.execute("INSERT INTO rooms VALUES('!room','group',?,NULL,30)",(u['alice']['mxid'],))
        local.execute('INSERT INTO files VALUES(?,?,?,?,?,?,0)',(fid,'!room',u['alice']['subject'],len(data),START/1000,(END+30*86400000)/1000))
    seeded(s,[fid])
    for spec in archive.SPECS:archive.seal_day(s,'!room',DAY,spec,END+1)
    later=END+29*86400000
    archive.ingest_batch(s.archive_ingest,[event(4,later,content={archive.ATTACHMENT_KEY:[fid]})],4,later+1)
    now=END+30*86400000
    archive.ingest_batch(s.archive_ingest,[],4,now)
    with archive.connect(postgres['owner']) as c:c.execute('UPDATE archive_audit SET history_done=true')
    assert cleanup_entities(s,'!room',DAY,now)
    assert (s.objects/fid).read_bytes()==data
    with connect(s.db) as local:assert local.execute('SELECT removed FROM files WHERE id=?',(fid,)).fetchone()[0]==0

def test_empty_full_day_binds_date_and_retains_redacted_ciphertext(env):
    from datetime import timedelta
    s,_,_=env
    seeded(s)
    first=archive.seal_day(s,'!room',DAY,'events-manifest',END+1000)
    empty_day=DAY+timedelta(days=1);_,empty_end=archive.day_bounds(empty_day)
    archive.ingest_batch(s.archive_ingest,[],3,empty_end+1,[(empty_day,3,empty_end+1)])
    assert any(r['day']==empty_day and r['room']=='!room' for r in archive.pending_days(s))
    empty=archive.seal_day(s,'!room',empty_day,'events-manifest',empty_end+1)
    assert empty['event_count']==0 and empty['sha256']!=first['sha256']
    with zipfile.ZipFile(archive.artifact_path(s.archive_path,'!room',empty_day,'events-manifest')) as z:
        header=json.loads(z.read('archive.json'));assert header['day']==str(empty_day)
        assert z.read('events.ndjson')==b''
    with zipfile.ZipFile(archive.artifact_path(s.archive_path,'!room',DAY,'events-manifest')) as z:
        data=z.read('events.ndjson');assert b'CIPHER-2' in data and b'm.room.redaction' in data
