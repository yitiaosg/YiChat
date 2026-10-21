"""YiChat archive v1. No sync response, key material, downloader metadata or plaintext
claim can enter this format. Persisted Synapse receive time and stream position are
the authoritative event day and order. PostgreSQL roles separate ingest/seal/read.
"""
import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
import psycopg
from psycopg.rows import dict_row

VERSION=1
SPECS=('events-manifest','with-attachments')
PERMISSION_SPEC='joined-whole-utc-day-v1'
ATTACHMENT_KEY='org.yitiao.attachments'


class LateSealedEvent(RuntimeError):
    def __init__(self,event_id,room,day):
        self.event_id,self.room,self.day=event_id,room,day
        super().__init__('Late source event for sealed date; full-date evidence quarantined')


def canonical(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')


def sha256(data):return hashlib.sha256(data).hexdigest()


def utcday(ms):return datetime.fromtimestamp(ms/1000,timezone.utc).date()


def day_bounds(day):
    d=date.fromisoformat(str(day))
    start=int(datetime(d.year,d.month,d.day,tzinfo=timezone.utc).timestamp()*1000)
    return start,start+86400000


def connect(dsn):
    if not dsn:raise RuntimeError('Archive PostgreSQL DSN is required')
    return psycopg.connect(dsn,autocommit=False,row_factory=dict_row,options='-c search_path=yichat,public -c TimeZone=UTC',connect_timeout=5)


def artifact_path(root,room,day,spec):
    if spec not in SPECS:raise ValueError('Unknown archive spec')
    day=date.fromisoformat(str(day)).isoformat()
    return Path(root)/sha256(room.encode())/day/('v1-'+spec+'.zip')


def ingest_batch(dsn,rows,through,observed_ms,fences=()):
    try:return _ingest_batch(dsn,rows,through,observed_ms,fences)
    except LateSealedEvent as e:
        with connect(dsn) as c:
            c.execute('INSERT INTO archive_gaps VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',('late:'+e.event_id,e.room,e.day,'Late accepted source event after sealing; complete-date exports quarantined'))
        record_failure(dsn)
        raise


def _ingest_batch(dsn,rows,through,observed_ms,fences=()):
    """Append-only ingest and cursor advance commit together; every worker deduplicates.

    rows: (event_id, room, stream_ordering, received_ts, original persisted JSON).
    A fence is captured after UTC midnight from the global allocated stream ID,
    and is usable only when Synapse's contiguous persisted watermark reaches it.
    """
    with connect(dsn) as c:
        c.execute("SELECT pg_advisory_xact_lock(17919,1)")
        checkpoint=c.execute("SELECT * FROM checkpoints WHERE source='synapse'").fetchone()
        if checkpoint and checkpoint['observed_ms']>int(datetime.now(timezone.utc).timestamp()*1000):
            raise RuntimeError('Source clock moved backward; sealing paused')
        for event_id,room,seq,received,raw in rows:
            event=json.loads(raw)
            # unsigned contains volatile age and redaction decorations; canonical
            # persistence fields, content (including ciphertext), hashes and signatures remain.
            event.pop('unsigned',None)
            event['event_id']=event_id;event['room_id']=room
            body=canonical(event);day=utcday(received)
            old=c.execute('SELECT body_sha256 FROM events WHERE event_id=%s',(event_id,)).fetchone()
            if old:
                if old['body_sha256']!=sha256(body):raise RuntimeError('Accepted event changed at source')
                continue
            if c.execute('SELECT 1 FROM archives WHERE room=%s AND day=%s LIMIT 1',(room,day)).fetchone():
                raise LateSealedEvent(event_id,room,day)
            c.execute('INSERT INTO events VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (event_id,room,seq,received,day,event['sender'],event['type'],event.get('state_key'),body,sha256(body)))
            for fid in event.get('content',{}).get(ATTACHMENT_KEY,[]):
                receipt=c.execute('SELECT room,uploader FROM attachments WHERE id=%s',(fid,)).fetchone()
                if not receipt or receipt['room']!=room or receipt['uploader']!=event['sender']:
                    raise RuntimeError('Accepted attachment declaration has no matching authenticated upload')
                c.execute('INSERT INTO attachment_references VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(fid,event_id,room,day,received))
                c.execute('UPDATE attachments SET last_ref_ms=greatest(coalesce(last_ref_ms,0),%s) WHERE id=%s',(received,fid))
            if event['type']=='m.room.member':
                c.execute('INSERT INTO memberships VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (event_id,room,event['state_key'],seq,received,event['content']['membership']))
        for day,seq,at in fences:
            c.execute('INSERT INTO fences VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(day,seq,at))
        c.execute("INSERT INTO checkpoints VALUES('synapse',%s,%s,true,NULL) ON CONFLICT(source) DO UPDATE SET sequence=excluded.sequence,observed_ms=greatest(checkpoints.observed_ms,excluded.observed_ms),healthy=true,error=NULL",(through,observed_ms))


def reconcile_source_position(dsn,allocated):
    """Read the last actual accepted sequence, not a watermark containing gaps.
    SQLite may reuse unused tail allocations after restart. Accepted positions
    must never rewind; an actual source rollback fails closed.
    """
    with connect(dsn) as c:
        c.execute('SELECT pg_advisory_xact_lock(17919,1)')
        accepted=c.execute('SELECT greatest(coalesce((SELECT max(sequence) FROM events),0),coalesce((SELECT max(last_sequence) FROM archives),0)) n').fetchone()['n']
        if accepted>allocated:raise RuntimeError('Synapse source rolled back behind accepted archive evidence')
        checkpoint=c.execute("SELECT sequence FROM checkpoints WHERE source='synapse'").fetchone()
        if checkpoint and checkpoint['sequence']>allocated:
            # Only unused allocations can disappear in this branch. Preserve all
            # accepted IDs and sealed metadata; unfinished fences can use the
            # new source's complete allocation bound instead of a vanished gap.
            c.execute('UPDATE fences SET sequence=%s WHERE sequence>%s',(allocated,allocated))
        return accepted


def record_failure(dsn):
    with connect(dsn) as c:
        c.execute("INSERT INTO checkpoints VALUES('synapse',0,0,false,'ingestion failed; manual audit required') ON CONFLICT(source) DO UPDATE SET healthy=false,error=excluded.error")


def record_attachment(dsn,fid,room,uploader,received_ms,size,digest):
    with connect(dsn) as c:
        c.execute('SELECT pg_advisory_xact_lock(17919,1)')
        old=c.execute('SELECT sha256,bytes FROM attachments WHERE id=%s',(fid,)).fetchone()
        if old:
            if old['sha256']!=digest or old['bytes']!=size:raise RuntimeError('Attachment receipt conflict')
            c.execute('DELETE FROM upload_reservations WHERE id=%s',(fid,))
            return
        if c.execute('SELECT 1 FROM archives WHERE room=%s AND day=%s LIMIT 1',(room,utcday(received_ms))).fetchone():
            raise RuntimeError('Cannot append to sealed date')
        c.execute('INSERT INTO attachments(id,room,uploader,received_ms,day,bytes,sha256) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(fid,room,uploader,received_ms,utcday(received_ms),size,digest))
        c.execute('DELETE FROM upload_reservations WHERE id=%s',(fid,))


def reserve_upload(dsn,fid):
    with connect(dsn) as c:
        c.execute('INSERT INTO upload_reservations VALUES(%s,(extract(epoch FROM clock_timestamp())*1000)::bigint)',(fid,))


def release_upload(dsn,fid):
    with connect(dsn) as c:c.execute('DELETE FROM upload_reservations WHERE id=%s',(fid,))


def allowed(c,room,mxid,start_ms,end_ms):
    """Conservative joined visibility. No current-membership shortcut for old data."""
    before=c.execute('SELECT membership FROM memberships WHERE room=%s AND mxid=%s AND received_ms<=%s ORDER BY sequence DESC LIMIT 1',(room,mxid,start_ms)).fetchone()
    if not before or before['membership']!='join':return False
    # Joining after leaving within the day never grants the missing period.
    return not c.execute("SELECT 1 FROM memberships WHERE room=%s AND mxid=%s AND received_ms>%s AND received_ms<%s AND membership<>'join' LIMIT 1",(room,mxid,start_ms,end_ms)).fetchone()


def authorize(dsn,room,mxid,day):
    start,end=day_bounds(day)
    with connect(dsn) as c:
        if not allowed(c,room,mxid,start,end):raise PermissionError('仅完整日期具有历史访问权限的成员可下载整日归档')


def attachment_manifest(c,room,day):
    return c.execute('''SELECT a.id,a.bytes,a.sha256,a.received_ms,a.uploader,
        array_agg(r.event_id ORDER BY r.event_id COLLATE "C") event_ids
        FROM attachments a JOIN attachment_references r ON r.file=a.id
        WHERE r.room=%s AND r.day=%s GROUP BY a.id ORDER BY a.id''',(room,day)).fetchall()


def contents(c,room,day,spec):
    rows=c.execute('SELECT * FROM events WHERE room=%s AND day=%s ORDER BY sequence,event_id',(room,day)).fetchall()
    files=attachment_manifest(c,room,day)
    manifest=canonical({'attachments':files,'day':str(day),'room_id':room,'version':VERSION})
    header=canonical({'day':str(day),'format':'yichat-archive','permission_spec':PERMISSION_SPEC,'room_id':room,'spec':spec,'version':VERSION})
    events=b''.join(canonical({'event':json.loads(bytes(r['body'])),'event_id':r['event_id'],'received_ms':r['received_ms'],'room_id':room,'sender':r['sender'],'sequence':r['sequence']}) for r in rows)
    return rows,files,{'archive.json':header,'attachments.json':manifest,'events.ndjson':events},sha256(manifest)


def write_zip(target,entries,files,objects,include):
    """ZIP_STORED, sorted ASCII paths, fixed DOS time/perms, no comment/extra."""
    names=dict(entries)
    expected_by_path={'attachments/'+r['id']:r for r in files}
    if include:
        names.update({'attachments/'+r['id']:Path(objects)/r['id'] for r in files})
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
        z.comment=b''
        for name,value in sorted(names.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3;info.external_attr=0o100600<<16;info.compress_type=zipfile.ZIP_STORED
            expected=expected_by_path.get(name)
            digest=hashlib.sha256();total=0
            with (io.BytesIO(value) if isinstance(value,bytes) else value.open('rb')) as src,z.open(info,'w',force_zip64=True) as out:
                while chunk:=src.read(65536):digest.update(chunk);total+=len(chunk);out.write(chunk)
            if expected and (total!=expected['bytes'] or digest.hexdigest()!=expected['sha256']):
                raise RuntimeError('Missing/corrupt attachment; complete archive unavailable')


def spool_events(c,room,day,path):
    """Bound sealing memory independently of daily event count."""
    count=0;first=last=None
    with c.cursor(name='archive_event_stream') as cursor,Path(path).open('wb') as out:
        cursor.execute('SELECT * FROM events WHERE room=%s AND day=%s ORDER BY sequence,event_id',(room,day))
        for r in cursor:
            out.write(canonical({'event':json.loads(bytes(r['body'])),'event_id':r['event_id'],'received_ms':r['received_ms'],'room_id':room,'sender':r['sender'],'sequence':r['sequence']}))
            if first is None:first=r['sequence']
            last=r['sequence'];count+=1
    return count,first,last


def file_digest(path):
    h=hashlib.sha256();size=0
    with Path(path).open('rb') as f:
        while b:=f.read(1048576):h.update(b);size+=len(b)
    return h.hexdigest(),size


def seal_day(s,room,day,spec,now_ms=None,crash=None):
    if spec not in SPECS:raise ValueError('Invalid archive specification')
    now_ms=now_ms or int(datetime.now(timezone.utc).timestamp()*1000)
    start,end=day_bounds(day)
    if end>now_ms:raise ValueError('Current day is only a provisional snapshot')
    target=artifact_path(s.archive_path,room,day,spec)
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with connect(s.archive_sealer) as c:
        # Same lock as ingest prevents a sealer observing a partial batch.
        c.execute('SELECT pg_advisory_xact_lock(17919,1)')
        existing=c.execute('SELECT * FROM archives WHERE room=%s AND day=%s AND version=1 AND spec=%s',(room,day,spec)).fetchone()
        if existing:return existing
        checkpoint=c.execute("SELECT * FROM checkpoints WHERE source='synapse'").fetchone()
        fence=c.execute('SELECT * FROM fences WHERE day=%s',(day,)).fetchone()
        if not checkpoint or not checkpoint['healthy'] or not fence or fence['observed_ms']<end or checkpoint['sequence']<fence['sequence']:
            raise RuntimeError('Archive ingestion incomplete; sealing and cleanup paused')
        if now_ms-checkpoint['observed_ms']>120000:raise RuntimeError('Source heartbeat stale; sealing paused')
        if c.execute('SELECT 1 FROM upload_reservations WHERE created_ms<%s LIMIT 1',(end,)).fetchone():
            raise RuntimeError('Upload receipt outstanding; sealing paused')
        if c.execute('SELECT 1 FROM archive_gaps WHERE room=%s AND day=%s LIMIT 1',(room,day)).fetchone():
            raise RuntimeError('Legacy attachment evidence missing; cannot seal complete archive')
        files=attachment_manifest(c,room,day)
        manifest=canonical({'attachments':files,'day':str(day),'room_id':room,'version':VERSION})
        manifest_hash=sha256(manifest)
        entries={'archive.json':canonical({'day':str(day),'format':'yichat-archive','permission_spec':PERMISSION_SPEC,'room_id':room,'spec':spec,'version':VERSION}),'attachments.json':manifest}
        fd,tmp=tempfile.mkstemp(prefix='.sealing-',dir=target.parent);os.close(fd)
        event_fd,event_tmp=tempfile.mkstemp(prefix='.events-',dir=target.parent);os.close(event_fd)
        try:
            count,first,last=spool_events(c,room,day,event_tmp)
            entries['events.ndjson']=Path(event_tmp)
            write_zip(tmp,entries,files,s.objects,spec=='with-attachments')
            digest,size=file_digest(tmp)
            with open(tmp,'rb') as f:os.fsync(f.fileno())
            if crash:crash('before_rename')
            os.replace(tmp,target)
            directory=os.open(target.parent,os.O_RDONLY);os.fsync(directory);os.close(directory)
            if crash:crash('before_commit')
            result=c.execute('INSERT INTO archives(room,day,version,spec,sha256,bytes,event_count,first_sequence,last_sequence,attachment_sha256,state,permission_spec,fence_sequence) VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,\'sealed\',%s,%s) RETURNING *',
                (room,day,spec,digest,size,count,first,last,manifest_hash,PERMISSION_SPEC,fence['sequence'])).fetchone()
            c.execute('INSERT INTO archive_audit(room,day,version,spec,checked_at) VALUES(%s,%s,1,%s,now())',(room,day,spec))
            from .record_audit import seal_digest
            if spec=='events-manifest':seal_digest(s,c,room,day,result['sha256'])
            return result
        finally:
            Path(tmp).unlink(missing_ok=True)
            Path(event_tmp).unlink(missing_ok=True)


def summary(dsn,room,day,spec):
    if spec not in SPECS:raise ValueError('Invalid archive specification')
    with connect(dsn) as c:
        row=c.execute('SELECT a.*,u.cleaned_at,u.error,EXISTS(SELECT 1 FROM archive_gaps g WHERE g.room=a.room AND g.day=a.day) has_gaps FROM archives a LEFT JOIN archive_audit u USING(room,day,version,spec) WHERE room=%s AND day=%s AND version=1 AND spec=%s',(room,day,spec)).fetchone()
    return row


def pending_days(s):
    today=datetime.now(timezone.utc).date()
    with connect(s.archive_sealer) as c:
        return c.execute('''WITH first_days AS (
            SELECT room,min(day) first_day FROM (
                SELECT room,day FROM events UNION SELECT room,day FROM attachments UNION SELECT room,day FROM archives
            ) source GROUP BY room)
            SELECT f.room,g.day::date AS day FROM first_days f
            CROSS JOIN LATERAL generate_series(f.first_day::timestamp,(%s::date-1)::timestamp,interval '1 day') g(day)
            WHERE (SELECT count(*) FROM archives a WHERE a.room=f.room AND a.day=g.day::date AND a.version=1)<2
            ORDER BY day,room''',(today,)).fetchall()


def eligible_cleanup(s,room,day,now_ms):
    """No deletion before both full specifications are sealed, readable and verified.
    Boundary: UTC day end + 30*86400 seconds. Permissions/summary/ledger survive.
    """
    _,end=day_bounds(day)
    if now_ms<end+30*86400000:return False
    with connect(s.archive_cleanup) as c:
        checkpoint=c.execute("SELECT * FROM checkpoints WHERE source='synapse'").fetchone()
        if not checkpoint or not checkpoint['healthy']:raise RuntimeError('Ingest unhealthy: cleanup stopped')
        if now_ms-checkpoint['observed_ms']>120000:raise RuntimeError('Source heartbeat stale: cleanup stopped')
        if c.execute('SELECT 1 FROM archive_gaps WHERE room=%s AND day=%s LIMIT 1',(room,day)).fetchone():raise RuntimeError('Date evidence quarantined: cleanup stopped')
        rows=c.execute('SELECT a.*,u.cleaned_at,u.cleanup_started_at FROM archives a JOIN archive_audit u USING(room,day,version,spec) WHERE room=%s AND day=%s AND version=1',(room,day)).fetchall()
        if {r['spec'] for r in rows}!=set(SPECS):raise RuntimeError('Incomplete evidence: cleanup stopped')
        for r in rows:
            if r['cleaned_at'] or r['cleanup_started_at']:continue
            target=artifact_path(s.archive_path,room,day,r['spec'])
            if file_digest(target)!=(r['sha256'],r['bytes']):raise RuntimeError('Evidence mismatch: cleanup stopped')
        return True
