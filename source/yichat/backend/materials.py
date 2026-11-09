"""Immutable message documents. Material bytes never depend on a live preview URL."""
import asyncio,hashlib,json,re,secrets,time,os
from pathlib import Path
from fastapi import Request
from fastapi.responses import FileResponse
from . import archive,policy,membership,previews
from .db import connect,transaction
from .security import fail
KEY='org.yitiao.material'
SCHEMA='''CREATE TABLE IF NOT EXISTS message_materials(id TEXT PRIMARY KEY,room TEXT NOT NULL,sender TEXT NOT NULL,kind TEXT NOT NULL,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,created_ms INTEGER NOT NULL,expires_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS material_events(event TEXT PRIMARY KEY,material TEXT UNIQUE NOT NULL,room TEXT NOT NULL,sender TEXT NOT NULL,sequence INTEGER NOT NULL,received_ms INTEGER NOT NULL,finalized_ms INTEGER,sha256 TEXT,expires_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS material_event_room ON material_events(room,sequence);
CREATE TABLE IF NOT EXISTS protocol_removed_events(event TEXT PRIMARY KEY,room TEXT NOT NULL,removed_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_material_cleanup(event TEXT PRIMARY KEY,room TEXT NOT NULL,sender TEXT NOT NULL,redaction TEXT,state TEXT NOT NULL DEFAULT 'pending');'''
TTL=7*86400000

def directory(s):return s.objects.parent/'message-materials'
def migrate(s):
 with connect(s.db) as c:c.executescript(SCHEMA)
 directory(s).mkdir(parents=True,exist_ok=True,mode=0o700)
def body_hash(raw):return hashlib.sha256(raw).hexdigest()
def recognized(content):
 if content.get(KEY):return False
 if content.get('org.yitiao.material_source') or content.get('org.yitiao.record'):return True
 if isinstance(content.get('org.yitiao.forward_snapshot'),dict):
  prior=content['org.yitiao.forward_snapshot']
  return bool(prior.get('previews')) or recognized(prior.get('content') or {})
 if content.get('msgtype') in ('m.image','m.file','m.video','m.audio'):return True
 if content.get('org.yitiao.no_preview'):return False
 text=str(content.get('body',''))+' '+str((content.get('org.yitiao.share') or {}).get('url',''))
 return any(previews.parse(u.rstrip('，。！？、；：）】》」』.,!?;:)')) or re.fullmatch(r'https://chat\.yitiao\.org/(gifts/[a-f0-9]{32}|records/[a-f0-9]{64})',u.rstrip('，。！？、；：）】》」』.,!?;:)')) for u in re.findall(r'https://[^\s<>"\u3000]+',text))
def legacy_url(content):
 if content.get(KEY) or content.get('org.yitiao.no_preview') or content.get('msgtype') in ('m.image','m.file','m.audio','m.video'):return False
 previous=content.get('org.yitiao.forward_snapshot')
 if isinstance(previous,dict) and (previous.get('content') or {}).get('msgtype') in ('m.image','m.file','m.audio','m.video'):return False
 return recognized(content)

def files(s,content):
 from .sharing import file_ids
 result=[]
 with connect(s.db) as c:
  for fid in sorted(file_ids(content)):
   row=c.execute('SELECT f.*,h.sha256 FROM files f JOIN file_hashes h USING(id) WHERE id=?',(fid,)).fetchone()
   if not row or row['removed'] or not (s.objects/fid).is_file():fail(410,'消息附件已删除')
   result.append({'id':fid,'sha256':row['sha256'],'bytes':str(row['bytes'])})
 return result

def freeze(app,s,who,room,content):
 from .sharing import snapshot
 from . import public_records
 if content.get('msgtype') in ('m.image','m.file','m.audio','m.video'):
  return {'version':2,'kind':'media','content':content,'previews':[]}
 if content.get('org.yitiao.record') or re.fullmatch(r'https://chat\.yitiao\.org/records/[a-f0-9]{64}',str(content.get('body','')).strip()):
  h=content.get('org.yitiao.record') or content['body'].strip().rsplit('/',1)[1]
  try:record=public_records.public(s,h)
  except __import__('fastapi').HTTPException as e:
   if e.status_code!=404 or not content.get('org.yitiao.record'):raise
   record=public_records.public(s,h,pending=True)
   if record['payload'].get('owner')!=who['mxid'] or content.get('org.yitiao.record_json')!=record['canonical']:fail(403,'尚未发布的记录只能由其创建者确认发送')
  if content.get('org.yitiao.record'):
   with connect(s.db) as c:allowed=c.execute('SELECT 1 FROM shared_record_rooms WHERE hash=? AND room=?',(h,room)).fetchone()
   if not allowed:fail(403,'聊天记录未授权到此会话')
  return {'version':2,'kind':'record','content':content,'record':{'hash':h,'canonical':record['canonical'],'payload':record['payload'],'expires':record['expires']},'previews':[]}
 item=snapshot(app,s,who,{'content':content,'room_id':room,'sender':who['mxid'],'event_id':'$material','origin_server_ts':int(time.time()*1000)})
 if not item['previews']:fail(410,'分享原始内容不可用，请重新从原页面分享')
 return {'version':2,'kind':'share','content':content,'previews':item['previews']}

async def prepare(app,s,who,room,content):
 from .sharing import access,file_ids,grant_files,eligible,individual_allowed
 info=access(s,who,room);policy.require_send(s,info,who['mxid'])
 if not isinstance(content,dict) or content.get(KEY):fail(400,'需要原始分享或文件内容')
 value=None
 source=content.get('org.yitiao.material_source')
 if source is not None:
  if not isinstance(source,str):fail(400,'需要原始消息ID')
  original=authorize(s,who,source);events=eligible(s,who,original['room'],[source]);original_content=events[0]['content']
  if not original_content.get(KEY) or not individual_allowed(original_content):fail(403,'此原始消息不能逐条转发')
  meta=state(s,source);raw=(directory(s)/meta['id']).read_bytes()
  if body_hash(raw)!=meta['payload_sha256']:fail(503,'原始消息存证不一致')
  value=json.loads(raw);content=dict(value['content']);content['org.yitiao.attachments']=[f['id'] for f in value.get('files',[])]
 for fid in file_ids(content):
  with connect(s.db) as c:row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
  if not row:fail(410,'附件已删除')
  await app.state.file_access(who,row)
 if value is None:value=freeze(app,s,who,room,content)
 if value['kind']=='record':
  record_ids=set().union(*(file_ids(m['content']) for m in value['record']['payload']['messages']))
  content=dict(content,**{'org.yitiao.attachments':sorted(file_ids(content)|record_ids)})
  value['content']=content
 value['files']=files(s,content)
 raw=archive.canonical(value)
 if len(raw)>32*1024*1024:fail(413,'原始分享数据过大')
 directory(s).mkdir(parents=True,exist_ok=True,mode=0o700)
 mid=secrets.token_hex(24);now=int(time.time()*1000);target=directory(s)/mid
 with connect(s.db) as c:identical=c.execute('SELECT id FROM message_materials WHERE sha256=? LIMIT 1',(body_hash(raw),)).fetchone()
 if identical and (directory(s)/identical['id']).is_file():os.link(directory(s)/identical['id'],target)
 else:
  temporary=target.with_suffix('.part');temporary.write_bytes(raw);temporary.chmod(0o600);temporary.replace(target)
 try:
  with transaction(s.db) as c:c.execute('INSERT INTO message_materials VALUES(?,?,?,?,?,?,?,?)',(mid,room,who['mxid'],value['kind'],body_hash(raw),len(raw),now,now+TTL))
 except BaseException:target.unlink(missing_ok=True);raise
 grant_files(s,who,room,[content])
 summary='聊天记录' if value['kind']=='record' else content.get('body','附件') if value['kind']=='media' else {'gift':'赠送闪卡','card':'分享闪卡','order':'收款回执' if content.get('org.yitiao.share',{}).get('type')=='receipt' else '转账 / 红包','doudizhu_record':'斗地主战绩'}.get(value['previews'][0]['type'],value['previews'][0].get('title','分享消息'))
 result={'msgtype':content.get('msgtype','m.text') if value['kind']=='media' else 'm.text','body':str(summary)[:2000],KEY:{'version':2,'id':mid,'sha256':body_hash(raw),'kind':value['kind']},'org.yitiao.attachments':sorted(file_ids(content))}
 if value['kind']=='media':result.update({k:v for k,v in content.items() if k in ('url','info')})
 if value['kind']=='share':
  first=value['previews'][0];typ=first['type'];result['org.yitiao.material_type']='receipt' if first.get('receipt') else typ
  if typ in ('gift','order'):
   bid=first['data'].get('id') if typ=='order' else first.get('url','').rsplit('/',1)[-1]
   result['org.yitiao.business']={'kind':typ,'id':bid}
 for key in ('org.yitiao.business','m.relates_to'):
  if key in content:result[key]=content[key]
 return result

def validate(s,room,sender,content):
 ref=content.get(KEY)
 if ref is None:return
 if not isinstance(ref,dict) or set(ref)!={'version','id','sha256','kind'} or ref.get('version')!=2 or not re.fullmatch('[a-f0-9]{48}',str(ref.get('id',''))):fail(400,'消息原始数据描述无效')
 with connect(s.db) as c:row=c.execute('SELECT * FROM message_materials WHERE id=?',(ref['id'],)).fetchone()
 if not row or row['room']!=room or row['sender']!=sender or row['sha256']!=ref['sha256'] or row['kind']!=ref['kind'] or row['expires_ms']<=int(time.time()*1000):fail(403,'原始消息未获服务器授权或已经过期')
 path=directory(s)/ref['id']
 if not path.is_file() or body_hash(path.read_bytes())!=row['sha256']:fail(503,'服务器原始消息校验失败')

def observe(s,rows):
 now=int(time.time()*1000);activations=[]
 with transaction(s.db) as c:
  for eid,room,seq,at,raw in rows:
   event=json.loads(raw);content=event.get('content') or {};ref=content.get(KEY)
   if not isinstance(ref,dict):continue
   row=c.execute('SELECT id FROM message_materials WHERE id=? AND room=? AND sender=?',(ref.get('id'),room,event.get('sender'))).fetchone()
   if row:
    c.execute('INSERT OR IGNORE INTO material_events(event,material,room,sender,sequence,received_ms,expires_ms) VALUES(?,?,?,?,?,?,?)',(eid,row['id'],room,event['sender'],seq,at,at+TTL))
    c.execute('UPDATE message_materials SET expires_ms=? WHERE id=?',(at+TTL,row['id']))
    activations.append((row['id'],room))
 for mid,room in activations:
  value=json.loads((directory(s)/mid).read_text())
  if value.get('kind')=='record':
   from .public_records import activate_message
   activate_message(s,room,value['content'])

def final_projection(row,payload_hash,file_rows):
 return {'event_id':row['event'],'format':'yichat-material-v2','payload_sha256':payload_hash,'room_id':row['room'],'sender':row['sender'],'shared_at':str(row['received_ms']),'files':file_rows,'version':'2'}
def final_hash(row,payload_hash,file_rows):return body_hash(archive.canonical(final_projection(row,payload_hash,file_rows)))
def state(s,eid,now_ms=None):
 now=int(time.time()*1000) if now_ms is None else now_ms
 with connect(s.db) as c:
  row=c.execute('SELECT e.*,m.sha256 payload_sha256,m.bytes,m.kind FROM material_events e JOIN message_materials m ON m.id=e.material WHERE e.event=?',(eid,)).fetchone()
 if not row or row['expires_ms']<=now:fail(410,'原始消息及单条摘要已超过七天保留期')
 row=dict(row);raw=(directory(s)/row['material']).read_bytes();payload=json.loads(raw)
 if body_hash(raw)!=row['payload_sha256']:fail(503,'原始消息存证损坏')
 final_at=row['received_ms']+(0 if payload.get('content',{}).get('org.yitiao.business') or any(p.get('type') in ('gift','order') for p in payload.get('previews',[])) else 300000)
 final=now>=final_at
 digest=final_hash(row,row['payload_sha256'],payload.get('files',[])) if final else None
 if final:
  with transaction(s.db) as c:
   old=c.execute('SELECT sha256 FROM material_events WHERE event=?',(eid,)).fetchone()
   if old and old['sha256'] and old['sha256']!=digest:fail(503,'不可撤回消息摘要发生变化')
   c.execute('UPDATE material_events SET finalized_ms=?,sha256=? WHERE event=?',(final_at,digest,eid))
 return {'event_id':eid,'id':row['material'],'room_id':row['room'],'sender':row['sender'],'shared_at':str(row['received_ms']),'expires_ms':row['expires_ms'],'payload_sha256':row['payload_sha256'],'sha256':digest,'finalized':final,'files':payload.get('files',[]),'kind':row['kind']}

def saved_projection(s,eid):
 """Sealer uses the saved payload/file digests, without rehashing original bytes."""
 with connect(s.db) as c:row=c.execute('SELECT e.*,m.sha256 payload_sha256 FROM material_events e JOIN message_materials m ON m.id=e.material WHERE e.event=?',(eid,)).fetchone()
 if not row:fail(410,'原始消息单条摘要已清理')
 row=dict(row);payload=json.loads((directory(s)/row['material']).read_text());files=payload.get('files',[])
 digest=final_hash(row,row['payload_sha256'],files)
 if row['sha256'] and row['sha256']!=digest:fail(503,'已保存的原件时间摘要发生变化')
 with transaction(s.db) as c:c.execute('UPDATE material_events SET sha256=?,finalized_ms=coalesce(finalized_ms,?) WHERE event=?',(digest,row['received_ms']+300000,eid))
 return {'sha256':digest,'files':files}

def authorize(s,who,eid):
 with connect(s.db) as c:row=c.execute('SELECT * FROM history_receipts WHERE event_id=?',(eid,)).fetchone()
 if not row:fail(503,{'code':'YICHAT_MATERIAL_PENDING','error':'原始消息正在接受存证，请稍后重试'})
 with archive.connect(s.archive_reader) as pg:
  if not archive.allowed(pg,row['room'],who['mxid'],row['received_ms'],row['received_ms']+1):fail(403,'没有消息历史访问权限')
 return row

def cleanup(s,now_ms=None,include_ledger=False):
 now=int(time.time()*1000) if now_ms is None else now_ms
 with transaction(s.db) as c:
  expired=c.execute('SELECT id FROM message_materials WHERE expires_ms<=? AND NOT EXISTS(SELECT 1 FROM material_events e WHERE e.material=message_materials.id AND e.expires_ms>?)',(now,now)).fetchall()
  c.execute('DELETE FROM material_events WHERE expires_ms<=?',(now,))
  for row in expired:(directory(s)/row['id']).unlink(missing_ok=True);c.execute('DELETE FROM message_materials WHERE id=?',(row['id'],))
 if include_ledger:
  with transaction(s.ledger) as c:c.execute('DELETE FROM yichat_gift_renderings WHERE id IN (SELECT id FROM yichat_card_gifts WHERE created<?)',(now/1000-7*86400,))
 return len(expired)

def install(app,s,session):
 def identity(request):return app.state.api_owner(request) if request.url.path.startswith('/open/v1/') else session(request)
 @app.post('/open/v1/conversations/{room}/share')
 @app.post('/api/messages/{room}/share')
 async def share(request:Request,room:str):
  who=identity(request);data=await request.json()
  if request.url.path.startswith('/open/v1/'):app.state.api_writable(who,room)
  if not isinstance(data,dict) or set(data)-{'url','content','txn_id'}:fail(400,'需要 url 或 content 以及可选 txn_id')
  content=data.get('content') if 'content' in data else {'msgtype':'m.text','body':data.get('url')}
  if not isinstance(content,dict) or not recognized(content):fail(400,'此消息不包含可保存的原始分享内容')
  return await publish(app,s,who,room,content,data.get('txn_id'))
 @app.get('/api/materials/removed')
 async def removed(request:Request,after:int=0):
  from .history import current_rooms,accessible
  who=session(request);rooms=current_rooms(s,who)
  if after<0:fail(400,'无效清理游标')
  with connect(s.db) as c:rows=c.execute('SELECT h.* FROM history_receipts h JOIN protocol_removed_events p ON p.event=h.event_id WHERE h.sequence>? ORDER BY h.sequence LIMIT 200',(after,)).fetchall()
  with archive.connect(s.archive_reader) as pg:visible=[{'event_id':r['event_id'],'room':r['room'],'sequence':r['sequence']} for r in rows if accessible(pg,rooms,who,r,s)]
  return {'records':visible,'next_after':rows[-1]['sequence'] if len(rows)==200 else None}
 @app.post('/open/v1/materials/check')
 @app.post('/api/materials/check')
 async def check(request:Request):
  who=identity(request);data=await request.json();rows=data.get('messages',[]) if isinstance(data,dict) else []
  if not isinstance(rows,list) or not 1<=len(rows)<=100:fail(400,'每次核验1～100条原始消息')
  result=[]
  for row in rows:
   if not isinstance(row,dict) or not isinstance(row.get('event_id'),str) or len(row['event_id'])>255:fail(400,'需要有效消息ID')
   eid=row.get('event_id');authorize(s,who,eid)
   try:meta=state(s,eid)
   except __import__('fastapi').HTTPException as e:
    if e.status_code==410:result.append({'event_id':eid,'state':'expired'});continue
    raise
   matched=row.get('payload_sha256')==meta['payload_sha256']
   prefix='/open/v1/materials/' if request.url.path.startswith('/open/v1/') else '/api/materials/'
   result.append(meta|{'state':'ok' if matched else 'replace','download':None if matched else prefix+meta['id']+'?event='+__import__('urllib.parse',fromlist=['quote']).quote(eid,safe='')})
  return {'messages':result}
 @app.get('/open/v1/materials/{mid}')
 @app.get('/api/materials/{mid}')
 async def read(request:Request,mid:str,event:str):
  who=identity(request);authorize(s,who,event);meta=state(s,event)
  if request.url.path.startswith('/open/v1/'):
   from .security import request_limit
   request_limit(s,'api-media:'+who['subject'],limit=1)
  if mid!=meta['id']:fail(403,'原始数据与消息不匹配')
  return FileResponse(directory(s)/mid,media_type='application/json',headers={'X-YiChat-SHA256':meta['payload_sha256']})
 @app.post('/open/v1/materials/{event:path}/confirm')
 @app.post('/api/materials/{event:path}/confirm')
 async def confirm(request:Request,event:str):
  who=identity(request);authorize(s,who,event);data=await request.json();meta=state(s,event)
  return meta|{'state':'ok' if data.get('payload_sha256')==meta['payload_sha256'] and (not meta['finalized'] or data.get('sha256')==meta['sha256']) else 'mismatch'}

async def publish(app,s,who,room,content,txn=None):
 from . import server_messages
 if not isinstance(txn,str) or not re.fullmatch('[A-Za-z0-9._-]{1,64}',txn):
  if txn is not None:fail(400,'txn_id 无效')
  txn=secrets.token_hex(16)
 aid='material:'+who['subject']+':'+room+':'+txn;request_hash=body_hash(archive.canonical(content))
 with connect(s.db) as c:old=c.execute('SELECT * FROM server_announcements WHERE id=?',(aid,)).fetchone()
 if old:
  original=json.loads(old['content'])
  if original.get('org.yitiao.request_hash')!=request_hash:fail(409,'此事务已绑定其他分享内容')
  if old['event']:return {'event_id':old['event'],'conversation_id':room,'txn_id':txn}
 else:
  prepared=await prepare(app,s,who,room,content);prepared.update({'org.yitiao.server_event':aid,'org.yitiao.request_hash':request_hash})
  with transaction(s.db) as c:c.execute('INSERT OR IGNORE INTO server_announcements(id,room,sender,content) VALUES(?,?,?,?)',(aid,room,who['mxid'],json.dumps(prepared,ensure_ascii=False)))
 await server_messages.tick(app)
 with connect(s.db) as c:row=c.execute('SELECT event FROM server_announcements WHERE id=?',(aid,)).fetchone()
 if not row or not row['event']:fail(503,{'code':'YICHAT_PUBLICATION_PENDING','error':'原始分享已保存，发送正在重试；请沿用同一事务ID'})
 return {'event_id':row['event'],'conversation_id':room,'txn_id':txn}
