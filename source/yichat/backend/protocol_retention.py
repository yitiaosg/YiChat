"""Seven-day material/source expiry through Synapse's own event-expiry method.
Old URL previews are an explicit, narrowly staged one-time migration.
"""
import asyncio,json,time
from datetime import datetime,timezone
import httpx
from . import archive,materials
from .db import connect,transaction
SCHEMA='''CREATE TABLE IF NOT EXISTS protocol_expiry_jobs(event TEXT PRIMARY KEY,room TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS protocol_file_expiry_jobs(id TEXT PRIMARY KEY);'''

def initialize(s):
 with connect(s.db) as c:
  if c.execute("SELECT 1 FROM packet_protocol_meta WHERE key='migration_prepared'").fetchone():return
 found=[];last_refs={}
 with archive.connect(s.archive_reader) as pg:
  last_refs={r['id']:r['at'] for r in pg.execute('SELECT id,greatest(received_ms,coalesce(last_ref_ms,0)) at FROM attachments').fetchall()}
  with pg.cursor(name='legacy_material_scan') as stream:
   stream.execute("SELECT event_id,room,sender,body FROM events WHERE type='m.room.message' ORDER BY sequence")
   for row in stream:
    content=json.loads(bytes(row['body'])).get('content',{})
    if content.get(materials.KEY) or content.get('msgtype') in ('m.image','m.file','m.video','m.audio') or content.get('org.yitiao.no_preview'):continue
    if materials.legacy_url(content):found.append((row['event_id'],row['room'],row['sender']))
 with transaction(s.db) as c:
  now=int(time.time()*1000)
  for row in c.execute('SELECT id,completed,expires FROM files WHERE removed=0').fetchall():
   expiry=max(row['completed'],last_refs.get(row['id'],0)/1000)+7*86400
   c.execute('UPDATE files SET expires=min(expires,?) WHERE id=?',(expiry,row['id']))
  for eid,room,sender in found:
   c.execute('INSERT OR IGNORE INTO legacy_material_cleanup(event,room,sender) VALUES(?,?,?)',(eid,room,sender))
   c.execute('INSERT OR IGNORE INTO protocol_expiry_jobs(event,room,kind) VALUES(?,?,?)',(eid,room,'legacy'))
   c.execute('INSERT OR IGNORE INTO protocol_removed_events VALUES(?,?,?)',(eid,room,now))
  c.execute("INSERT OR REPLACE INTO packet_protocol_meta VALUES('migration_prepared',?)",(str(now),))
  if not found:c.execute("INSERT OR REPLACE INTO packet_protocol_meta VALUES('legacy_cleanup_complete',?)",(str(now),))

def ready(s):
 with connect(s.db) as c:return bool(c.execute("SELECT 1 FROM packet_protocol_meta WHERE key='legacy_cleanup_complete'").fetchone())
def stage_expiry(s,now_ms=None):
 now=int(time.time()*1000) if now_ms is None else now_ms;cutoff=now-materials.TTL
 with connect(s.db) as c:
  rows=c.execute("SELECT event_id,room,received_ms FROM history_receipts WHERE received_ms<=? AND sha256<>'' AND NOT EXISTS(SELECT 1 FROM protocol_removed_events p WHERE p.event=history_receipts.event_id)",(cutoff,)).fetchall()
 with transaction(s.db) as c:
  for row in rows:
   day=str(archive.utcday(row['received_ms']))
   if c.execute('SELECT 1 FROM packet_day_roots WHERE room=? AND day=?',(row['room'],day)).fetchone():c.execute('INSERT OR IGNORE INTO protocol_expiry_jobs(event,room,kind) VALUES(?,?,?)',(row['event_id'],row['room'],'expiry'))

def finish_entities(s):
 with connect(s.db) as c:rows=c.execute("SELECT event,room,kind FROM protocol_expiry_jobs WHERE state='censored' LIMIT 500").fetchall()
 if not rows:return 0
 ids=[r['event'] for r in rows]
 with archive.connect(s.archive_cleanup) as pg:pg.execute('DELETE FROM events WHERE event_id=ANY(%s)',(ids,))
 with transaction(s.db) as c:
  for row in rows:
   c.execute("UPDATE history_receipts SET sha256='' WHERE event_id=?",(row['event'],))
   c.execute("UPDATE server_announcements SET content='{}',upstream=NULL WHERE event=?",(row['event'],))
   c.execute('DELETE FROM api_send_grants WHERE event=?',(row['event'],))
   c.execute("UPDATE protocol_expiry_jobs SET state='done' WHERE event=?",(row['event'],))
   if row['kind']=='legacy':c.execute("UPDATE legacy_material_cleanup SET state='done' WHERE event=?",(row['event'],))
 return len(rows)

def expire_files(s,now_ms=None):
 now=int(time.time()*1000) if now_ms is None else now_ms
 with connect(s.db) as c:sealed={(r['room'],r['day']) for r in c.execute('SELECT room,day FROM packet_day_roots')}
 with archive.connect(s.archive_reader) as pg:refs=pg.execute('SELECT r.file,r.room,r.day,r.event_id FROM attachment_references r JOIN events e ON e.event_id=r.event_id').fetchall()
 protected={r['file'] for r in refs if (r['room'],str(r['day'])) not in sealed}
 with transaction(s.db) as c:
  rows=c.execute('SELECT id FROM files WHERE removed=0 AND expires*1000<=? AND NOT EXISTS(SELECT 1 FROM attachment_admissions a WHERE a.file=files.id)',(now,)).fetchall()
  for row in rows:
   if row['id'] in protected:continue
   c.execute('INSERT OR IGNORE INTO protocol_file_expiry_jobs VALUES(?)',(row['id'],))
   (s.objects/row['id']).unlink(missing_ok=True);c.execute('UPDATE files SET removed=1 WHERE id=?',(row['id'],));c.execute('DELETE FROM file_hashes WHERE id=?',(row['id'],));c.execute('DELETE FROM file_reuse_grants WHERE file=?',(row['id'],))
 with connect(s.db) as c:pending=[r['id'] for r in c.execute('SELECT id FROM protocol_file_expiry_jobs LIMIT 200')]
 if pending:
  with archive.connect(s.archive_cleanup) as pg:
   pg.execute('DELETE FROM attachment_references WHERE file=ANY(%s)',(pending,));pg.execute('DELETE FROM attachments WHERE id=ANY(%s)',(pending,))
  with transaction(s.db) as c:
   for fid in pending:c.execute('DELETE FROM protocol_file_expiry_jobs WHERE id=?',(fid,))
 return len(rows)

async def run_once(s,migration=False):
 await asyncio.to_thread(initialize,s)
 if migration:
  with transaction(s.db) as c:c.execute("INSERT OR REPLACE INTO packet_protocol_meta VALUES('legacy_cleanup_authorized','1')")
 with connect(s.db) as c:authorized=bool(c.execute("SELECT 1 FROM packet_protocol_meta WHERE key='legacy_cleanup_authorized'").fetchone())
 await asyncio.to_thread(stage_expiry,s)
 with connect(s.db) as c:jobs=c.execute("SELECT event FROM protocol_expiry_jobs WHERE state='pending' AND (kind='expiry' OR ?) LIMIT 100",(int(authorized),)).fetchall()
 if jobs:
  async with httpx.AsyncClient(headers={'Authorization':'Bearer '+s.admin_token},trust_env=False,timeout=60) as client:
   response=await client.post(s.synapse+'/_synapse/client/yichat/material-expiry',json={'events':[r['event'] for r in jobs]});response.raise_for_status()
 await asyncio.to_thread(finish_entities,s)
 with connect(s.db) as c:pending=c.execute("SELECT count(*) FROM protocol_expiry_jobs WHERE kind='legacy' AND state<>'done'").fetchone()[0]
 if authorized and not pending:
  with transaction(s.db) as c:
   completed=c.execute("SELECT 1 FROM packet_protocol_meta WHERE key='legacy_records_cleaned'").fetchone()
   if not completed:
    prepared=c.execute("SELECT value FROM packet_protocol_meta WHERE key='migration_prepared'").fetchone()
    before=int(prepared[0])/1000
    hashes=[r[0] for r in c.execute('SELECT hash FROM shared_records WHERE created<=?',(before,))]
    for h in hashes:c.execute('DELETE FROM shared_record_rooms WHERE hash=?',(h,));c.execute('DELETE FROM record_publications WHERE hash=?',(h,));c.execute('DELETE FROM shared_records WHERE hash=?',(h,))
    c.execute("INSERT OR REPLACE INTO packet_protocol_meta VALUES('legacy_records_cleaned','1')")
   c.execute("INSERT OR REPLACE INTO packet_protocol_meta VALUES('legacy_cleanup_complete',?)",(str(int(time.time()*1000)),))
  # Retire old ZIP bodies without rewriting their immutable historical roots.
  for path in s.archive_path.rglob('v1-*.zip'):path.unlink(missing_ok=True)
 await asyncio.to_thread(materials.cleanup,s)
 await asyncio.to_thread(expire_files,s)
 return {'legacy_pending':pending,'censored_batch':len(jobs),'ready':ready(s)}
