"""Durable, idempotent announcement outbox for server-computed order receipts."""
import json,time,hashlib,logging
from urllib.parse import quote
from .db import connect,transaction
from . import api_grants,policy
SCHEMA='''CREATE TABLE IF NOT EXISTS server_announcements(id TEXT PRIMARY KEY,room TEXT NOT NULL,sender TEXT NOT NULL,content TEXT NOT NULL,upstream TEXT,event TEXT);
CREATE TABLE IF NOT EXISTS server_scan_cursor(kind TEXT PRIMARY KEY,last_row INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS server_announcement_grants(id TEXT PRIMARY KEY,room TEXT NOT NULL,sender TEXT NOT NULL,digest TEXT NOT NULL,expires REAL NOT NULL);'''
def migrate(s):
 with connect(s.db) as c:c.executescript(SCHEMA)
def granted(s,room,sender,typ,content):
 if typ!='m.room.message':return False
 with connect(s.db) as c:return bool(c.execute('SELECT 1 FROM server_announcement_grants WHERE room=? AND sender=? AND digest=? AND expires>?',(room,sender,api_grants.content_digest(content),time.time())).fetchone())
def queue(s,order,receipt=False):
 uid=order['recipient_uid'] if receipt else order['sender_uid']
 with connect(s.db) as c:identity=c.execute('SELECT mxid FROM identities WHERE subject=?',(uid,)).fetchone()
 if not identity:return
 url='https://chat.yitiao.org/orders/'+order['id'];kind='receipt' if receipt else 'order';aid=kind+':'+order['id']
 content={'msgtype':'m.text','body':('已收款 · ' if receipt else '')+url,'org.yitiao.share':{'type':kind,'url':url,'id':order['id']},'org.yitiao.business':{'kind':'order','id':order['id']},'org.yitiao.server_event':aid}
 with transaction(s.db) as c:c.execute('INSERT OR IGNORE INTO server_announcements(id,room,sender,content) VALUES(?,?,?,?)',(aid,order['room'],identity[0],json.dumps(content,ensure_ascii=False)))
async def tick(app):
 s=app.state.settings
 # Cursor-based recovery covers the commit/queue crash gap without rescanning history.
 for kind in ('order','receipt'):
  with connect(s.db) as c:cursor=c.execute('SELECT last_row FROM server_scan_cursor WHERE kind=?',(kind,)).fetchone()
  after=cursor[0] if cursor else 0
  with connect(s.ledger) as c:
   if kind=='order':rows=c.execute("SELECT o.rowid scan_row,o.id,o.room,p.sender_uid,p.recipient_uid FROM yichat_orders o JOIN yichat_order_participants p ON p.order_id=o.id WHERE o.deposit LIKE 'internal:%' AND o.rowid>? ORDER BY o.rowid LIMIT 100",(after,)).fetchall()
   else:rows=c.execute("SELECT pay.rowid scan_row,o.id,o.room,p.sender_uid,p.recipient_uid FROM yichat_payouts pay JOIN yichat_orders o ON o.id=pay.order_id JOIN yichat_order_participants p ON p.order_id=o.id WHERE o.deposit LIKE 'internal:%' AND o.kind='direct' AND pay.kind='claim' AND pay.rowid>? ORDER BY pay.rowid LIMIT 100",(after,)).fetchall()
  for row in rows:
   queue(s,dict(row),kind=='receipt')
   with transaction(s.db) as c:c.execute('INSERT INTO server_scan_cursor VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET last_row=excluded.last_row',(kind,row['scan_row']))
 with connect(s.db) as c:jobs=c.execute('SELECT * FROM server_announcements WHERE event IS NULL LIMIT 20').fetchall()
 for job in jobs:
  try:
   content=json.loads(job['content']);policy.require_send(s,policy.room(s,job['room']),job['sender'])
   # A prior accepted event is authoritative even if the response was lost.
   from . import archive
   with archive.connect(s.archive_reader) as pg:found=pg.execute("SELECT event_id FROM events WHERE room=%s AND sender=%s AND type='m.room.message' AND convert_from(body,'UTF8')::jsonb->'content'->>'org.yitiao.server_event'=%s LIMIT 1",(job['room'],job['sender'],job['id'])).fetchone()
   if found:
    with transaction(s.db) as c:c.execute('UPDATE server_announcements SET event=? WHERE id=?',(found['event_id'],job['id']))
    continue
   upstream=job['upstream']
   if not upstream:
    auth=await app.state.matrix.call('POST','/_synapse/admin/v1/users/'+quote(job['sender'],safe='')+'/login',data={'valid_until_ms':int(time.time()*1000)+86400000})
    upstream=app.state.cipher.encrypt(auth['access_token'].encode()).decode()
    with transaction(s.db) as c:c.execute('UPDATE server_announcements SET upstream=? WHERE id=?',(upstream,job['id']))
   with transaction(s.db) as c:c.execute('INSERT OR REPLACE INTO server_announcement_grants VALUES(?,?,?,?,?)',(job['id'],job['room'],job['sender'],api_grants.content_digest(content),time.time()+120))
   txn=hashlib.sha256(job['id'].encode()).hexdigest();path='/_matrix/client/v3/rooms/'+quote(job['room'],safe='')+'/send/m.room.message/server-'+txn
   result=await app.state.matrix.call('PUT',path,app.state.cipher.decrypt(upstream.encode()).decode(),content)
   with transaction(s.db) as c:c.execute('UPDATE server_announcements SET event=?,upstream=NULL WHERE id=?',(result['event_id'],job['id']));c.execute('DELETE FROM server_announcement_grants WHERE id=?',(job['id'],))
  except Exception as e:
   logging.getLogger('yichat.announcements').warning('Server announcement pending retry (%s)',getattr(e,'status_code',type(e).__name__))
   if getattr(e,'status_code',None)==401:
    with transaction(s.db) as c:c.execute('UPDATE server_announcements SET upstream=NULL WHERE id=?',(job['id'],))
