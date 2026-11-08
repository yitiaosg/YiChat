"""Server-verified message selection, room sharing preferences and file reuse."""
import hashlib,hmac,json,re,secrets,time
from fastapi import Request
from .db import connect,transaction
from .security import fail
from . import policy,membership,archive,previews,conversations
SCHEMA='''CREATE TABLE IF NOT EXISTS room_share_preferences(room TEXT NOT NULL,uid TEXT NOT NULL,blocked INTEGER NOT NULL,PRIMARY KEY(room,uid));
CREATE TABLE IF NOT EXISTS shared_records(hash TEXT PRIMARY KEY,payload TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS shared_record_rooms(hash TEXT NOT NULL,room TEXT NOT NULL,PRIMARY KEY(hash,room));
CREATE TABLE IF NOT EXISTS file_reuse_grants(file TEXT NOT NULL,room TEXT NOT NULL,uid TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(file,room,uid));'''
def migrate(s):
 with connect(s.db) as c:c.executescript(SCHEMA)
def access(s,who,room):
 info=policy.room(s,room)
 with connect(s.ledger) as c:membership.require(c,room,who['mxid'])
 return info
def sharing(s,who,room):
 info=access(s,who,room)
 with connect(s.ledger) as c:members=[r[0] for r in c.execute("SELECT mxid FROM yichat_memberships WHERE room=? AND membership='join' AND pending_event IS NULL",(room,))]
 small=len(members)<=5
 with connect(s.db) as c:
  ids=[r[0] for r in c.execute('SELECT subject FROM identities WHERE mxid IN ('+','.join('?' for _ in members)+')',members)] if small else [policy.subject(s,info['owner'])]
  blocked=bool(ids and c.execute('SELECT 1 FROM room_share_preferences WHERE room=? AND blocked=1 AND uid IN ('+','.join('?' for _ in ids)+')',(room,*ids)).fetchone())
  mine=c.execute('SELECT blocked FROM room_share_preferences WHERE room=? AND uid=?',(room,who['subject'])).fetchone()
 return {'allowed':not blocked,'blocked_by_me':bool(mine and mine[0]),'can_set':small or who['mxid']==info['owner']}
def eligible(s,who,room,ids):
 access(s,who,room)
 if not isinstance(ids,list) or not 1<=len(ids)<=100 or len(set(ids))!=len(ids) or any(not isinstance(e,str) or not e.startswith('$') for e in ids):fail(400,'请选择1～100条不同消息')
 with archive.connect(s.archive_reader) as pg:
  from .open_api import require_archive_ready
  require_archive_ready(pg)
  rows=pg.execute("SELECT event_id,room,received_ms,body,body_sha256 FROM events WHERE room=%s AND event_id = ANY(%s) AND type='m.room.message' ORDER BY sequence",(room,ids)).fetchall()
  if len(rows)!=len(ids):fail(410,'所选消息已从服务器删除或不可分享')
  result=[]
  for row in rows:
   if int(time.time()*1000)-row['received_ms']<=300000:fail(409,'消息发送满5分钟后才能选择')
   if not archive.allowed(pg,room,who['mxid'],row['received_ms'],row['received_ms']+1):fail(403,'没有此记录的访问权限')
   raw=bytes(row['body'])
   if hashlib.sha256(raw).hexdigest()!=row['body_sha256']:fail(503,'消息存证校验失败')
   event=json.loads(raw);content=event['content']
   if content.get('org.yitiao.record') or '/records/' in str(content.get('body','')):fail(400,'聊天记录消息不能参与多选或再次合并')
   with connect(s.db) as c:
    if c.execute('SELECT 1 FROM history_receipts WHERE room=? AND redacts=?',(room,row['event_id'])).fetchone():fail(410,'不能选择已撤回消息')
   result.append(event)
 return result
def file_ids(content):
 ids=set(content.get('org.yitiao.attachments',[]));url=content.get('url','');m=re.fullmatch(r'mxc://chat\.yitiao\.org/([a-f0-9]{48})',url) if isinstance(url,str) else None
 if m:ids.add(m[1])
 return ids
def grant_files(s,who,room,contents):
 with transaction(s.db) as c:
  for content in contents:
   for fid in file_ids(content):
    row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    if not row or row['removed'] or row['expires']<=time.time() or not (s.objects/fid).is_file():fail(410,'所选附件已从服务器删除')
    c.execute('INSERT INTO file_reuse_grants VALUES(?,?,?,?) ON CONFLICT(file,room,uid) DO UPDATE SET expires=max(expires,excluded.expires)',(fid,room,who['subject'],row['expires']))
def snapshot(app,s,who,event):
 content=event['content'];urls=[]
 from .notification_summary import URL,TRAILING
 for u in URL.findall(content.get('body','')):
  u=u.rstrip(TRAILING)
  if previews.parse(u):urls.append(u)
 metas=[]
 for url in urls[:4]:
  parsed=previews.parse(url)
  if parsed[0]=='order':
   with connect(s.ledger) as c:
    o=c.execute('SELECT * FROM yichat_orders WHERE id=? AND room=?',(parsed[1][0],event['room_id'])).fetchone()
    meta={'type':'order','url':url,'data':app.state.funds.public(c,o,who['username'])} if o else None
   if meta:meta['data']['actions']={};meta['data']['authorize_url']=None;meta['receipt']=content.get('org.yitiao.share',{}).get('type')=='receipt'
  else:
   try:meta=previews.metadata(s,url)
   except Exception:meta=None
  if meta:metas.append(meta)
 return {'id':event['event_id'],'sender':event['sender'],'name':policy.username(s,policy.subject(s,event['sender'])),'sent_at':event.get('origin_server_ts'),'content':content,'previews':metas}
def sign(s,room,sender,content):
    value=archive.canonical([room,sender,content])
    return hmac.new(s.session_key.encode(),value,hashlib.sha256).hexdigest()
def validate(s,room,sender,content):
    if content.get('org.yitiao.record') is not None:
        h=content['org.yitiao.record'];raw=content.get('org.yitiao.record_json')
        if not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h) or not isinstance(raw,str) or hashlib.sha256(raw.encode()).hexdigest()!=h:fail(400,'聊天记录哈希无效')
        with connect(s.db) as c:row=c.execute('SELECT payload FROM shared_records JOIN shared_record_rooms USING(hash) WHERE hash=? AND room=?',(h,room)).fetchone()
        if not row or row[0]!=raw:fail(403,'此聊天记录未获服务器授权')
    if content.get('org.yitiao.forward_snapshot') is not None:
        proof=content.get('org.yitiao.forward_proof');unsigned={k:v for k,v in content.items() if k!='org.yitiao.forward_proof'}
        if not isinstance(proof,str) or not hmac.compare_digest(proof,sign(s,room,sender,unsigned)):fail(403,'转发快照签名无效')

def install(app,s,session):
 @app.get('/api/sharing/{room}')
 async def preferences(request:Request,room:str):return sharing(s,session(request),room)
 @app.put('/api/sharing/{room}')
 async def preference_set(request:Request,room:str):
  who=session(request);current=sharing(s,who,room);data=await request.json()
  if not current['can_set']:fail(403,'超过5人的群聊只有群主可以设置分享权限')
  if not isinstance(data,dict) or set(data)!={'blocked'} or type(data['blocked'])!=bool:fail(400,'需要 blocked 布尔值')
  with transaction(s.db) as c:c.execute('INSERT OR REPLACE INTO room_share_preferences VALUES(?,?,?)',(room,who['subject'],int(data['blocked'])))
  return sharing(s,who,room)
 @app.get('/api/sharing/{room}/eligible')
 async def eligibility(request:Request,room:str,event:str):
  eligible(s,session(request),room,[event]);return {'eligible':True}
 @app.post('/api/sharing/{room}/selection')
 async def selection(request:Request,room:str):
  who=session(request);data=await request.json();events=eligible(s,who,room,data.get('ids'));mode=data.get('mode','image')
  if mode not in ('image','individual','combined'):fail(400,'分享方式无效')
  if mode!='individual' and not sharing(s,who,room)['allowed']:fail(403,'此会话已禁止分享图片和合并记录')
  if mode=='image' and len(events)>=50:fail(400,'长图最多49条消息')
  items=[snapshot(app,s,who,e) for e in events]
  if mode=='image':return {'messages':items}
  target=data.get('target');info=access(s,who,target);policy.require_send(s,info,who['mxid'])
  grant_files(s,who,target,[e['content'] for e in events])
  if mode=='individual':
   messages=[]
   for item in items:
    content={k:v for k,v in item['content'].items() if k not in ('org.yitiao.business','m.relates_to','org.yitiao.server_event','org.yitiao.forward_snapshot','org.yitiao.forward_proof')}
    content['org.yitiao.attachments']=sorted(file_ids(content));content['org.yitiao.forward_snapshot']=item
    content['org.yitiao.forward_proof']=sign(s,target,who['mxid'],content)
    if len(archive.canonical(content))>60000:fail(413,'单条转发消息过大，请减少预览内容')
    messages.append(content)
   return {'messages':messages}
  payload={'version':1,'messages':items};canonical=archive.canonical(payload)
  if len(canonical)>48000:fail(413,'合并记录过大，请减少选择的消息')
  h=hashlib.sha256(canonical).hexdigest();url='https://chat.yitiao.org/records/'+h
  with transaction(s.db) as c:
   c.execute('INSERT INTO shared_records VALUES(?,?,?) ON CONFLICT(hash) DO UPDATE SET created=excluded.created',(h,canonical.decode(),time.time()));c.execute('INSERT OR IGNORE INTO shared_record_rooms VALUES(?,?)',(h,target))
  content={'msgtype':'m.text','body':url,'org.yitiao.record':h,'org.yitiao.record_json':canonical.decode(),'org.yitiao.attachments':sorted(set().union(*(file_ids(e['content']) for e in events)))}
  if len(archive.canonical(content))>60000:fail(413,'合并记录过大，请减少选择的消息')
  return {'hash':h,'content':content}
 @app.get('/api/records/{h}')
 async def record(request:Request,h:str,room:str):
  who=session(request);access(s,who,room)
  with connect(s.db) as c:row=c.execute('SELECT payload FROM shared_records r JOIN shared_record_rooms a ON a.hash=r.hash WHERE r.hash=? AND a.room=?',(h,room)).fetchone()
  if not row:fail(404,'聊天记录不存在或无权查看')
  return {'hash':h,'payload':json.loads(row[0]),'canonical':row[0]}
 @app.post('/api/files/{room}/reuse')
 async def reuse(request:Request,room:str):
  who=session(request);info=access(s,who,room);policy.require_send(s,info,who['mxid']);data=await request.json();h=data.get('sha256')
  if not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h):fail(400,'需要 SHA-256')
  # Hashes are hints, never proof of access: only reuse a file already owned by this UID.
  with connect(s.db) as c:row=c.execute('SELECT f.* FROM files f JOIN file_hashes h USING(id) WHERE h.sha256=? AND f.uploader=? AND f.removed=0 AND f.expires>? LIMIT 1',(h,who['subject'],time.time())).fetchone()
  if not row:return {'found':False}
  grant_files(s,who,room,[{'org.yitiao.attachments':[row['id']]}])
  return {'found':True,'id':row['id'],'url':'mxc://'+s.server_name+'/'+row['id'],'bytes':row['bytes'],'expires':row['expires']}
