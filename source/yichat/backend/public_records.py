"""Explicit public record publications; access ends with the original messages."""
import hashlib,json,re,time
from pathlib import Path
from fastapi import Request
from fastapi.responses import FileResponse,Response
from .db import connect,transaction
from .security import fail
from . import archive
SCHEMA='''CREATE TABLE IF NOT EXISTS record_publications(
 hash TEXT PRIMARY KEY,selection_key TEXT NOT NULL UNIQUE,source_room TEXT NOT NULL,
 source_ids TEXT NOT NULL,expires REAL NOT NULL,active INTEGER NOT NULL DEFAULT 0);'''
IDENTITIES=('avatar','name','both','none')
def identity(data):
 value=data.get('identity','both')
 if value not in IDENTITIES:fail(400,'身份显示选项无效')
 return value
def url(h):return 'https://chat.yitiao.org/records/'+h
def migrate(c):
 if 'active' not in {r[1] for r in c.execute('PRAGMA table_info(record_publications)')}:c.execute('ALTER TABLE record_publications ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
def public(s,h,pending=False):
 if not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h):fail(404,'聊天记录不存在')
 with connect(s.db) as c:row=c.execute('SELECT p.*,r.payload FROM record_publications p JOIN shared_records r USING(hash) WHERE hash=?',(h,)).fetchone()
 if not row:
  # Earlier merged messages already have immutable, server-authorized snapshots.
  # Derive their source proof without changing the existing canonical hash.
  with connect(s.db) as c:old=c.execute('SELECT payload FROM shared_records WHERE hash=?',(h,)).fetchone()
  if not old:fail(404,'公开聊天记录不存在')
  payload=json.loads(old['payload']);ids=[m['id'] for m in payload.get('messages',[])]
  if not ids:fail(404,'聊天记录为空')
  with archive.connect(s.archive_reader) as pg:
   from .open_api import require_archive_ready
   require_archive_ready(pg)
   sources=pg.execute('SELECT room,event_id,received_ms FROM events WHERE event_id=ANY(%s)',(ids,)).fetchall()
  if len(sources)!=len(ids) or len({r['room'] for r in sources})!=1:fail(410,'引用消息已删除')
  expires=min(r['received_ms']/1000 for r in sources)+7*86400
  if expires<=time.time():fail(410,'引用消息已满七天，分享已失效')
  with transaction(s.db) as c:
   c.execute('INSERT OR IGNORE INTO record_publications VALUES(?,?,?,?,?,1)',(h,'legacy:'+h,sources[0]['room'],json.dumps(ids),expires))
   row=c.execute('SELECT p.*,r.payload FROM record_publications p JOIN shared_records r USING(hash) WHERE hash=?',(h,)).fetchone()
 if not row['active'] and not pending:fail(404,'此记录尚未发布')
 if row['expires']<=time.time():fail(410,'引用消息已满七天，分享已失效')
 ids=json.loads(row['source_ids'])
 with archive.connect(s.archive_reader) as pg:
  from .open_api import require_archive_ready
  require_archive_ready(pg)
  rows=pg.execute('SELECT event_id,received_ms FROM events WHERE room=%s AND event_id=ANY(%s)',(row['source_room'],ids)).fetchall()
 if len(rows)!=len(ids) or any(r['received_ms']/1000+7*86400<=time.time() for r in rows):fail(410,'引用消息已删除或超过七天')
 with connect(s.db) as c:
  if c.execute('SELECT 1 FROM history_receipts WHERE redacts IN ('+','.join('?' for _ in ids)+') LIMIT 1',ids).fetchone():fail(410,'引用消息已撤回')
 raw=row['payload']
 if hashlib.sha256(raw.encode()).hexdigest()!=h:fail(503,'记录哈希校验失败')
 return {'hash':h,'url':url(h),'canonical':raw,'payload':json.loads(raw),'expires':row['expires'],'active':bool(row['active'])}
def source(s,who,room,events,display):
 ids=[e['event_id'] for e in events]
 with archive.connect(s.archive_reader) as pg:rows=pg.execute('SELECT received_ms FROM events WHERE room=%s AND event_id=ANY(%s)',(room,ids)).fetchall()
 if len(rows)!=len(ids):fail(410,'引用消息已删除')
 expires=min(r['received_ms']/1000 for r in rows)+7*86400
 if expires<=time.time():fail(410,'公开分享仅支持七天内的消息')
 key=hashlib.sha256(archive.canonical([room,who['mxid'],ids,display])).hexdigest()
 return ids,expires,key

def draft(app,s,who,room,events,display):
 ids,expires,key=source(s,who,room,events,display)
 with connect(s.db) as c:existing=c.execute('SELECT r.payload,p.expires FROM record_publications p JOIN shared_records r USING(hash) WHERE selection_key=?',(key,)).fetchone()
 if existing:payload=json.loads(existing['payload']);expires=existing['expires']
 else:
  from .sharing import snapshot
  payload={'version':2,'owner':who['mxid'],'identity':display,'messages':[snapshot(app,s,who,e) for e in events]}
 if len(archive.canonical(payload))>256000:fail(413,'分享记录过大，请减少所选消息')
 # Preview contains no record hash/URL and persists no publication.
 return {'payload':payload,'messages':payload['messages'],'expires':expires}

def prepare(app,s,who,room,events,display,activate=False):
 ids,expires,key=source(s,who,room,events,display)
 with connect(s.db) as c:existing=c.execute('SELECT hash FROM record_publications WHERE selection_key=?',(key,)).fetchone()
 if existing:
  h=existing[0]
  if activate:
   with transaction(s.db) as c:c.execute('UPDATE record_publications SET active=1 WHERE hash=?',(h,))
  return public(s,h,pending=True)
 payload=draft(app,s,who,room,events,display)['payload'];raw=archive.canonical(payload);h=hashlib.sha256(raw).hexdigest()
 with transaction(s.db) as c:
  existing=c.execute('SELECT hash FROM record_publications WHERE selection_key=?',(key,)).fetchone()
  if existing:
   h=existing[0]
   if activate:c.execute('UPDATE record_publications SET active=1 WHERE hash=?',(h,))
  else:
   c.execute('INSERT INTO shared_records VALUES(?,?,?) ON CONFLICT(hash) DO NOTHING',(h,raw.decode(),time.time()))
   c.execute('INSERT INTO record_publications VALUES(?,?,?,?,?,?)',(h,key,room,json.dumps(ids),expires,int(activate)))
 return public(s,h,pending=True)

def activate_message(s,room,content):
 h=content.get('org.yitiao.record');raw=content.get('org.yitiao.record_json')
 if not isinstance(h,str) or not isinstance(raw,str):return
 with transaction(s.db) as c:
  admitted=c.execute('SELECT 1 FROM shared_records r JOIN shared_record_rooms a USING(hash) WHERE hash=? AND room=? AND payload=?',(h,room,raw)).fetchone()
  if admitted:c.execute('UPDATE record_publications SET active=1 WHERE hash=?',(h,))

def cleanup(c):
 expired='SELECT hash FROM record_publications WHERE expires<'+str(time.time())
 c.execute('DELETE FROM shared_record_rooms WHERE hash IN ('+expired+')')
 c.execute('DELETE FROM shared_records WHERE hash IN ('+expired+')')
 c.execute('DELETE FROM record_publications WHERE expires<?',(time.time(),))
def install(app,s):
 @app.get('/api/public-records/{h}')
 async def record(h:str):return public(s,h)
 @app.get('/api/public-records/{h}/files/{fid}/content')
 async def file(h:str,fid:str):
  record=public(s,h)
  from .sharing import file_ids
  if fid not in set().union(*(file_ids(m['content']) for m in record['payload']['messages'])):fail(404,'附件不属于此记录')
  with connect(s.db) as c:row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
  if not row or row['removed'] or row['expires']<=time.time() or not (s.objects/fid).is_file():fail(410,'附件已删除或过期')
  return FileResponse(s.objects/fid,media_type='application/octet-stream',headers={'Cache-Control':'no-store'})
 @app.get('/api/public-records/{h}/avatars/{index}')
 async def avatar(h:str,index:int):
  record=public(s,h);items=record['payload']['messages']
  if not 0<=index<len(items):fail(404,'头像不存在')
  embedded=items[index].get('avatar_data','')
  if isinstance(embedded,str) and len(embedded)<=180000 and re.fullmatch(r'data:image/jpeg;base64,[A-Za-z0-9+/=]+',embedded):
   import base64
   try:return Response(base64.b64decode(embedded.split(',',1)[1],validate=True),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
   except ValueError:fail(503,'原始头像数据损坏')
  username=items[index].get('username')
  if not username:
   from . import policy
   try:username=policy.username(s,policy.subject(s,items[index]['sender']))
   except Exception:fail(404,'未设置头像')
  root=(s.site/'static/avatar').resolve();target=(root/(username+'.jpg')).resolve()
  if not target.is_relative_to(root) or not target.is_file():fail(404,'头像不存在')
  return FileResponse(target,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
