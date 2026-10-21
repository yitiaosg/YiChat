"""Isolated sealer and evidence-first retention worker. No finance credentials.

Synapse purge is an admin API task. The worker never writes Synapse's tables.
The source adapter's read-only depth fence prevents origin_server_ts from deciding
which events expire. Current state and necessary crypto state remain in Synapse.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime,timezone,timedelta
from . import archive
from .config import Settings
from .db import connect,transaction
import httpx

log=logging.getLogger('yichat.archive')


async def submit_purge(s,http,room,event_id):
    from urllib.parse import quote
    response=await http.post(s.synapse+'/_synapse/admin/v1/purge_history/'+quote(room,safe=''),
        json={'purge_up_to_event_id':event_id,'delete_local_events':True})
    response.raise_for_status()
    return response.json()['purge_id']


def synchronize_files(s):
    with connect(s.db) as c:
        receipts=c.execute('SELECT f.*,h.sha256,i.mxid FROM files f JOIN file_hashes h USING(id) JOIN identities i ON i.subject=f.uploader WHERE f.removed=0').fetchall()
        active={r['id'] for r in c.execute('SELECT id FROM upload_leases WHERE expires>?',(time.time(),))}
        known={r['id'] for r in c.execute('SELECT id FROM files')}
    for r in receipts:
        archive.record_attachment(s.archive_ingest,r['id'],r['room'],r['mxid'],int(r['completed']*1000),r['bytes'],r['sha256'])
    with archive.connect(s.archive_ingest) as c:
        pending=c.execute('SELECT id,created_ms FROM upload_reservations').fetchall()
        for r in pending:
            # Only a completed recovery sweep may release an abandoned upload.
            if r['id'] not in active and r['id'] not in known and r['created_ms']<int((time.time()-3600)*1000):
                c.execute('DELETE FROM upload_reservations WHERE id=%s',(r['id'],))
                (s.objects/(r['id']+'.part')).unlink(missing_ok=True)
                (s.objects/r['id']).unlink(missing_ok=True)


def cleanup_entities(s,room,day,now_ms,crash=None):
    if not archive.eligible_cleanup(s,room,day,now_ms):return False
    with archive.connect(s.archive_cleanup) as c:
        c.execute('SELECT pg_advisory_xact_lock(17919,1)')
        rows=c.execute('SELECT * FROM archive_audit WHERE room=%s AND day=%s AND version=1 FOR UPDATE',(room,day)).fetchall()
        if not rows or not all(r['history_done'] for r in rows):return False
        from .record_audit import seal_digest
        bound=c.execute("SELECT sha256 FROM archives WHERE room=%s AND day=%s AND version=1 AND spec='events-manifest'",(room,day)).fetchone()
        seal_digest(s,c,room,day,bound['sha256'])
        c.execute('UPDATE archive_audit SET cleanup_started_at=coalesce(cleanup_started_at,now()) WHERE room=%s AND day=%s AND version=1',(room,day))
        c.commit()
        c.execute('SELECT pg_advisory_xact_lock(17919,1)')
        ids=c.execute('''SELECT a.id,a.last_ref_ms FROM attachments a WHERE a.room=%s AND
            (a.day=%s OR EXISTS(SELECT 1 FROM attachment_references r WHERE r.file=a.id AND r.day=%s))''',(room,day,day)).fetchall()
        for r in ids:
            with transaction(s.db) as local:
                record=local.execute('SELECT expires FROM files WHERE id=?',(r['id'],)).fetchone()
                pending=local.execute('SELECT 1 FROM attachment_admissions WHERE file=? LIMIT 1',(r['id'],)).fetchone()
                ref_expiry=(r['last_ref_ms']//86400000+31)*86400 if r['last_ref_ms'] else 0
                if pending or (record and max(record['expires'],ref_expiry)*1000>now_ms):continue
                (s.objects/r['id']).unlink(missing_ok=True)
                local.execute('UPDATE files SET removed=1 WHERE id=?',(r['id'],))
        for spec in archive.SPECS:archive.artifact_path(s.archive_path,room,day,spec).unlink(missing_ok=True)
        for directory in (s.objects,archive.artifact_path(s.archive_path,room,day,archive.SPECS[0]).parent):
            fd=os.open(directory,os.O_RDONLY);os.fsync(fd);os.close(fd)
        if crash:crash()
        # Fsync deletion metadata before marking cleanup committed. A crash can
        # only leave missing entities with intact hashes, never a fabricated file.
        from . import history
        # Preserve authenticated per-packet hashes before deleting body entities.
        with c.cursor(name='history_receipt_cleanup') as cursor:
            cursor.execute('SELECT event_id,room,sequence,received_ms,body FROM events WHERE room=%s AND day=%s ORDER BY sequence',(room,day))
            while rows:=cursor.fetchmany(500):history.record_rows(s,[(r['event_id'],r['room'],r['sequence'],r['received_ms'],bytes(r['body'])) for r in rows])
        c.execute('DELETE FROM events WHERE room=%s AND day=%s',(room,day))
        # Keep small upload/ref/hash permission receipts. Another accepted day
        # can still reference the same object after the upload date is cleaned.
        c.execute('UPDATE archive_audit SET cleaned_at=coalesce(cleaned_at,now()),checked_at=now(),error=NULL WHERE room=%s AND day=%s AND version=1',(room,day))
    return True


def cleanup_unsent(s,now_ms):
    """Unsent uploads are owner-only staging objects, not conversation exports."""
    with archive.connect(s.archive_cleanup) as c:
        c.execute('SELECT pg_advisory_xact_lock(17919,1)')
        cp=c.execute("SELECT * FROM checkpoints WHERE source='synapse'").fetchone()
        if not cp or not cp['healthy'] or now_ms-cp['observed_ms']>120000:raise RuntimeError('Source unavailable: staging cleanup paused')
        rows=c.execute('SELECT a.id FROM attachments a WHERE a.received_ms<%s AND NOT EXISTS(SELECT 1 FROM attachment_references r WHERE r.file=a.id)',(now_ms-30*86400000,)).fetchall()
        for row in rows:
            with transaction(s.db) as local:
                f=local.execute('SELECT * FROM files WHERE id=?',(row['id'],)).fetchone()
                if not f or f['removed'] or f['expires']*1000>now_ms:continue
                if local.execute('SELECT 1 FROM attachment_admissions WHERE file=? LIMIT 1',(row['id'],)).fetchone():continue
                (s.objects/row['id']).unlink(missing_ok=True)
                local.execute('UPDATE files SET removed=1 WHERE id=?',(row['id'],))


async def purge_history(s,http,room,day,now_ms):
    """Only purge a contiguous topological prefix whose *receive dates* all expire.
    JSON depth was accepted by Synapse, not supplied by an archive downloader.
    If out-of-order topology blocks the prefix, retain extra history and alert.
    """
    if not archive.eligible_cleanup(s,room,day,now_ms):return
    with archive.connect(s.archive_cleanup) as c:
        jobs=c.execute('SELECT history_task,history_done FROM archive_audit WHERE room=%s AND day=%s AND version=1 ORDER BY spec',(room,day)).fetchall()
        if all(j['history_done'] for j in jobs):return
        counts=c.execute('SELECT event_count FROM archives WHERE room=%s AND day=%s AND version=1',(room,day)).fetchall()
        if len(counts)==2 and all(r['event_count']==0 for r in counts):
            # Complete empty dates have no source timeline history to purge.
            c.execute('UPDATE archive_audit SET history_done=true,error=NULL WHERE room=%s AND day=%s AND version=1',(room,day))
            return
        task=next((j['history_task'] for j in jobs if j['history_task']),None)
        _,end=archive.day_bounds(day)
        if task:
            r=await http.get(s.synapse+'/_synapse/admin/v1/purge_history_status/'+task)
            r.raise_for_status();status=r.json()['status']
            if status=='complete':c.execute('UPDATE archive_audit SET history_done=true,error=NULL WHERE room=%s AND day=%s AND version=1',(room,day))
            elif status=='failed':raise RuntimeError('Synapse history purge failed; entities retained')
            return
        # Every event before the boundary, including events from other dates,
        # must have reliable sealed evidence before Synapse is allowed to purge it.
        all_events=c.execute('SELECT event_id,day,received_ms,body FROM events WHERE room=%s ORDER BY sequence',(room,)).fetchall()
        retained=[r for r in all_events if r['received_ms']>=end]
        if not retained:
            # Keep the last event to preserve a live forward extremity.
            retained=all_events[-1:]
        if not retained:return
        boundary=min(retained,key=lambda r:json.loads(bytes(r['body'])).get('depth',0))
        depth=json.loads(bytes(boundary['body'])).get('depth',0)
        prefix=[r for r in all_events if json.loads(bytes(r['body'])).get('depth',0)<depth]
        for d in {r['day'] for r in prefix}:
            if not archive.eligible_cleanup(s,room,d,now_ms):raise RuntimeError('Unsealed or unexpired prefix; history purge postponed')
        if not prefix:
            # There is no safe prefix yet. Do not claim history deletion.
            return
        task=await submit_purge(s,http,room,boundary['event_id'])
        c.execute('UPDATE archive_audit SET history_task=%s WHERE room=%s AND day=%s AND version=1',(task,room,day))


async def run_once(s):
    # A recently healthy PG checkpoint alone cannot attest that a restarted
    # source has finished rollback/recovery checks. Require the live module too.
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+s.admin_token},trust_env=False,timeout=10) as probe:
        ready=await probe.get(s.synapse+'/_synapse/client/yichat/fence')
        ready.raise_for_status()
    await asyncio.to_thread(synchronize_files,s)
    with archive.connect(s.archive_sealer) as c:
        c.execute('SELECT pg_advisory_xact_lock(17919,1)')
        # The global sealing lock excludes any live sealer before stale private
        # spool files are reaped after a hard process crash.
        for pattern in ('*/*/.sealing-*','*/*/.events-*'):
            for path in s.archive_path.glob(pattern):
                if path.stat().st_mtime<time.time()-3600:path.unlink(missing_ok=True)
    for row in archive.pending_days(s):
        for spec in archive.SPECS:
            try:
                await asyncio.to_thread(archive.seal_day,s,row['room'],row['day'],spec)
                status,error='sealed',None
            except Exception:
                log.exception('Archive incomplete for room/date; cleanup remains disabled')
                status,error='failed','Sealing failed; inspect service journal and repair evidence'
            with archive.connect(s.archive_sealer) as c:
                c.execute('INSERT INTO archive_jobs VALUES(%s,%s,1,%s,%s,%s,now()) ON CONFLICT(room,day,version,spec) DO UPDATE SET status=excluded.status,error=excluded.error,updated_at=now()',
                    (row['room'],row['day'],spec,status,error))
    now=int(time.time()*1000)
    await asyncio.to_thread(cleanup_unsent,s,now)
    with archive.connect(s.archive_cleanup) as c:
        dates=c.execute('SELECT DISTINCT room,day FROM archives a WHERE day<%s AND EXISTS(SELECT 1 FROM archive_audit u WHERE u.room=a.room AND u.day=a.day AND u.cleaned_at IS NULL) ORDER BY day,room',
            (datetime.now(timezone.utc).date()-timedelta(days=30),)).fetchall()
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+s.admin_token},trust_env=False,timeout=30) as http:
        for row in dates:
            await purge_history(s,http,row['room'],row['day'],now)
            await asyncio.to_thread(cleanup_entities,s,row['room'],row['day'],now)


async def main():
    s=Settings()
    while True:
        try:await run_once(s)
        except Exception:log.exception('Archive sealing/cleanup paused; evidence must be repaired')
        await asyncio.sleep(30)


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
