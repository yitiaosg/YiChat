"""Server-verified message selection, room sharing preferences and file reuse."""
import hashlib,hmac,json,re,secrets,time
from fastapi import Request
from .db import connect,transaction
from .security import fail
from . import policy,membership,archive,previews,conversations,public_records
SCHEMA='''CREATE TABLE IF NOT EXISTS room_share_preferences(room TEXT NOT NULL,uid TEXT NOT NULL,blocked INTEGER NOT NULL,PRIMARY KEY(room,uid));
CREATE TABLE IF NOT EXISTS shared_records(hash TEXT PRIMARY KEY,payload TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS shared_record_rooms(hash TEXT NOT NULL,room TEXT NOT NULL,PRIMARY KEY(hash,room));
CREATE TABLE IF NOT EXISTS file_reuse_grants(file TEXT NOT NULL,room TEXT NOT NULL,uid TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(file,room,uid));'''+public_records.SCHEMA
def migrate(s):
 with connect(s.db) as c:c.executescript(SCHEMA);public_records.migrate(c)
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
 if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(e,str) or not e.startswith('$') for e in ids) or len(set(ids))!=len(ids):fail(400,'请选择1～100条不同消息')
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
   with connect(s.db) as c:
    if c.execute('SELECT 1 FROM history_receipts WHERE room=? AND redacts=?',(room,row['event_id'])).fetchone():fail(410,'不能选择已撤回消息')
   result.append(event)
 return result
def record_message(content):
 if content.get('org.yitiao.material',{}).get('kind')=='record':return True
 return bool(content.get('org.yitiao.record') or re.search(r'https://chat\.yitiao\.org/records/[a-f0-9]{64}(?![a-zA-Z0-9/?#])',str(content.get('body',''))))
def individual_allowed(content):
 if content.get('org.yitiao.material_type') in ('gift','order','receipt'):return False
 share=content.get('org.yitiao.share') or {};business=content.get('org.yitiao.business') or {}
 if share.get('type') in ('order','receipt','gift') or business.get('kind') in ('order','receipt','gift'):return False
 if re.search(r'https://chat\.yitiao\.org/(orders|gifts)/[A-Za-z0-9_-]{32}',str(content.get('body',''))+' '+str(share.get('url',''))):return False
 previous=content.get('org.yitiao.forward_snapshot')
 return individual_allowed(previous['content']) if isinstance(previous,dict) and isinstance(previous.get('content'),dict) else True
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
 if content.get('org.yitiao.material'):
  from . import materials
  ref=content['org.yitiao.material'];raw=(materials.directory(s)/ref['id']).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=ref['sha256']:fail(503,'原始分享数据校验失败')
  doc=json.loads(raw);content=dict(doc['content']);content['org.yitiao.forward_snapshot']={'content':doc['content'],'previews':doc.get('previews',[])}
 previous=content.get('org.yitiao.forward_snapshot')
 frozen=previous if isinstance(previous,dict) and isinstance(previous.get('content'),dict) else None
 if frozen:content=frozen['content']
 share=content.get('org.yitiao.share') if isinstance(content.get('org.yitiao.share'),dict) else {}
 from .notification_summary import URL,TRAILING
 for u in URL.findall(content.get('body',''))+[str(share.get('url',''))]:
  u=u.rstrip(TRAILING)
  if previews.parse(u) and not content.get('org.yitiao.no_preview') and u not in urls:urls.append(u)
 metas=[]
 gift=re.search(r'https://chat\.yitiao\.org/gifts/([a-f0-9]{32})',str(content.get('body',''))+' '+str(share.get('url','')))
 gid=gift[1] if gift else content.get('org.yitiao.business',{}).get('id') if content.get('org.yitiao.business',{}).get('kind')=='gift' else None
 if gid:
  with connect(s.ledger) as c:
   row=c.execute('SELECT * FROM yichat_card_gifts WHERE id=? AND room=?',(gid,event['room_id'])).fetchone()
   current=c.execute('SELECT i.owner,EXISTS(SELECT 1 FROM yc_destroyed d WHERE d.instance=i.id) destroyed FROM yc_instances i WHERE i.id=?',(row['instance'],)).fetchone() if row else None
   saved=c.execute('SELECT payload FROM yichat_gift_renderings WHERE id=?',(gid,)).fetchone() if row else None
   if saved or current and not current['destroyed']:
    from .card_reads import store
    card=json.loads(saved['payload']) if saved else store(s).preview(row['instance'],current['owner'],connection=c)
    metas.append({'type':'gift','url':'https://chat.yitiao.org/gifts/'+gid,'title':'赠送卡片','data':{'card':card,'sender':row['sender'],'recipient':row['recipient']}})
 for url in urls[:4]:
  parsed=previews.parse(url)
  if parsed[0]=='order':
   with connect(s.ledger) as c:
    o=c.execute('SELECT * FROM yichat_orders WHERE id=? AND room=?',(parsed[1][0],event['room_id'])).fetchone()
    meta={'type':'order','url':url,'data':app.state.funds.public(c,o,who['username'])} if o else None
   if meta:meta['data']['actions']={};meta['data']['authorize_url']=None;meta['receipt']=share.get('type')=='receipt'
  else:
   try:meta=previews.metadata(s,url)
   except Exception:meta=None
  if meta:metas.append(meta)
 if frozen:
  metas=json.loads(json.dumps(frozen.get('previews',[])))
  for meta in metas:
   if meta.get('type')=='order':meta['data']['actions']={};meta['data']['authorize_url']=None
 # Keep new snapshots clean too, including older forwarded snapshots.
 unique={}
 for meta in metas:
  key=(meta.get('type'),meta.get('url'),(meta.get('data') or {}).get('id'),bool(meta.get('receipt')))
  unique.setdefault(key,meta)
 metas=list(unique.values())
 username=policy.username(s,policy.subject(s,event['sender']))
 from fastapi import HTTPException
 try:profile=app.state.public_profile(username)
 except HTTPException as exc:
  if exc.status_code!=404:raise
  profile={}
 result={'id':event['event_id'],'sender':event['sender'],'username':username,'name':profile.get('nickname') or username,'avatar_url':profile.get('avatar_url'),'sent_at':event.get('origin_server_ts'),'content':content,'previews':metas}
 if s.material_protocol and profile.get('avatar_url'):
  # A shared record carries a fixed avatar image too; offline viewing never
  # depends on the account's future avatar URL or its continued existence.
  root=(s.site/'static/avatar').resolve();target=(root/(username+'.jpg')).resolve()
  if target.is_relative_to(root) and target.is_file():
   import io,base64
   from PIL import Image
   try:
    with Image.open(target) as image:
     image.thumbnail((192,192));out=io.BytesIO();image.convert('RGB').save(out,format='JPEG',quality=85)
    data=out.getvalue()
    if len(data)<=131072:result['avatar_data']='data:image/jpeg;base64,'+base64.b64encode(data).decode()
   except (OSError,ValueError):pass
 return result
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

def set_sharing(s,who,room,data):
 current=sharing(s,who,room)
 if not current['can_set']:fail(403,'超过5人的群聊只有群主可以设置分享权限')
 if not isinstance(data,dict) or set(data)!={'blocked'} or type(data['blocked'])!=bool:fail(400,'需要 blocked 布尔值')
 with transaction(s.db) as c:c.execute('INSERT OR REPLACE INTO room_share_preferences VALUES(?,?,?)',(room,who['subject'],int(data['blocked'])))
 return sharing(s,who,room)

def install(app,s,session):
 public_records.install(app,s)
 @app.get('/api/sharing/{room}')
 async def preferences(request:Request,room:str):return sharing(s,session(request),room)
 @app.put('/api/sharing/{room}')
 async def preference_set(request:Request,room:str):return set_sharing(s,session(request),room,await request.json())
 @app.get('/api/sharing/{room}/eligible')
 async def eligibility(request:Request,room:str,event:str):
  eligible(s,session(request),room,[event]);return {'eligible':True}
 @app.post('/api/sharing/{room}/selection')
 async def selection(request:Request,room:str):
  return await select(session(request),room,await request.json())
 async def select(who,room,data):
  if not isinstance(data,dict):fail(400,'需要 JSON 对象')
  events=eligible(s,who,room,data.get('ids'));mode=data.get('mode','image')
  if mode not in ('image','individual','combined','prepare'):fail(400,'分享方式无效')
  if mode!='individual' and not sharing(s,who,room)['allowed']:fail(403,'此会话已禁止分享图片和合并记录')
  if mode=='image' and len(events)>=50:fail(400,'长图最多49条消息')
  # Reusing a known file ID is not itself proof of permission to publish it.
  for fid in set().union(*(file_ids(e['content']) for e in events)):
   with connect(s.db) as c:row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
   if not row or row['removed'] or row['expires']<=time.time() or not (s.objects/fid).is_file():fail(410,'所选附件已删除或过期')
   await app.state.file_access(who,row)
  if mode=='individual':
   if any(not individual_allowed(e['content']) for e in events):fail(400,'转账、收款、红包和赠卡消息不能逐条转发')
   target=data.get('target');info=access(s,who,target);policy.require_send(s,info,who['mxid'])
   grant_files(s,who,target,[e['content'] for e in events]);messages=[]
   for event in events:
    original=event['content']
    if original.get('org.yitiao.material'):
     messages.append({'msgtype':original.get('msgtype','m.text'),'body':original.get('body','分享消息'),'org.yitiao.material_source':event['event_id']});continue
    if isinstance(original.get('org.yitiao.forward_snapshot'),dict):original=original['org.yitiao.forward_snapshot'].get('content') or original
    content={k:v for k,v in original.items() if k in ('msgtype','body','url','info','format','formatted_body','org.yitiao.no_preview')}
    if original.get('org.yitiao.share',{}).get('url'):content['org.yitiao.share']={'url':original['org.yitiao.share']['url']}
    content['org.yitiao.attachments']=sorted(file_ids(original));messages.append(content)
   return {'messages':messages}
  if any(record_message(e['content']) for e in events):fail(400,'聊天记录不能再次合并或生成分享图片，可逐条转发原链接')
  display=public_records.identity(data)
  if mode=='prepare':return public_records.draft(app,s,who,room,events,display)
  if mode=='combined':
   info=access(s,who,data.get('target'));policy.require_send(s,info,who['mxid'])
  record=public_records.prepare(app,s,who,room,events,display,activate=mode=='image')
  if mode=='image':return record|{'messages':record['payload']['messages']}
  target=data.get('target');info=access(s,who,target);policy.require_send(s,info,who['mxid'])
  grant_files(s,who,target,[e['content'] for e in events]);h=record['hash']
  with transaction(s.db) as c:c.execute('INSERT OR IGNORE INTO shared_record_rooms VALUES(?,?)',(h,target))
  content={'msgtype':'m.text','body':record['url'],'org.yitiao.record':h,'org.yitiao.record_json':record['canonical'],'org.yitiao.attachments':sorted(set().union(*(file_ids(e['content']) for e in events)))}
  if len(archive.canonical(content))>60000:fail(413,'合并记录过大，请减少选择的消息；仍可生成分享图片')
  return record|{'content':content}
 app.state.share_selection=select
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
