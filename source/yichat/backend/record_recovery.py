"""A published hash grants access only to its selected immutable messages.
Payloads expire normally; online source-room Apps may relay verified originals.
"""
import base64,hashlib,json,re,secrets,threading,time
from fastapi import Request,HTTPException
from fastapi.responses import JSONResponse,Response
from . import archive,policy
from .db import connect,transaction
from .security import fail
SCHEMA='''CREATE TABLE IF NOT EXISTS record_capabilities(hash TEXT PRIMARY KEY,room TEXT NOT NULL,recipe TEXT NOT NULL,active INTEGER NOT NULL);'''

def remember(c,h,room,payload,active):
 recipe={'header':{k:v for k,v in payload.items() if k!='messages'},'messages':[{k:v for k,v in m.items() if k not in ('content','previews')} for m in payload['messages']]}
 from .sharing import file_ids
 fids=set().union(*(file_ids(m['content']) for m in payload['messages']))
 recipe['files']={}
 for fid in fids:
  f=c.execute('SELECT f.bytes,h.sha256 FROM files f JOIN file_hashes h USING(id) WHERE id=?',(fid,)).fetchone()
  if f:recipe['files'][fid]={'id':fid,'bytes':str(f['bytes']),'sha256':f['sha256']}
 c.execute('INSERT OR IGNORE INTO record_capabilities VALUES(?,?,?,?)',(h,room,json.dumps(recipe,ensure_ascii=False),int(active)))

def capability(s,h):
 if not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h):fail(404,'聊天记录不存在')
 with connect(s.db) as c:row=c.execute('SELECT * FROM record_capabilities WHERE hash=? AND active=1',(h,)).fetchone()
 if not row:fail(404,'聊天记录尚未发布或不存在')
 ids=[m['id'] for m in json.loads(row['recipe'])['messages']]
 with connect(s.db) as c:
  if any(c.execute('SELECT 1 FROM history_receipts WHERE room=? AND redacts=?',(row['room'],eid)).fetchone() for eid in ids):fail(410,'引用消息已撤回')
 if policy.retired(s,row['room']):fail(410,'原会话已清空，此分享已停止访问')
 return dict(row)

class Recovery:
 def __init__(self,app,s):self.app=app;self.s=s;self.jobs={};self.lock=threading.RLock()
 def prune(self):
  now=time.monotonic();self.jobs={k:v for k,v in self.jobs.items() if v['expires']>now}
 def job(self,h):
  self.prune()
  if h not in self.jobs:
   if len(self.jobs)>=128:fail(429,'聊天记录补齐队列已满，请稍后重试')
   self.jobs[h]={'expires':time.monotonic()+180,'canonical':None,'files':{},'wants':set()}
  return self.jobs[h]
 def record(self,h):
  cap=capability(self.s,h)
  with self.lock:
   job=self.job(h)
   if job['canonical']:return self.result(h,job['canonical'])
  # Try the source room before asking online devices. Preserve original display
  # identities; never refreeze a mutable URL as an allegedly original message.
  recipe=json.loads(cap['recipe']);ids=[m['id'] for m in recipe['messages']]
  with archive.connect(self.s.archive_reader) as pg:rows=pg.execute('SELECT event_id,body FROM events WHERE room=%s AND event_id=ANY(%s)',(cap['room'],ids)).fetchall()
  if len(rows)==len(ids):
   from .materials import KEY
   events={r['event_id']:json.loads(bytes(r['body'])) for r in rows};messages=[]
   try:
    for m in recipe['messages']:
     event=events[m['id']];content=event['content']
     if content.get(KEY):
      from . import materials
      meta=materials.state(self.s,m['id']);raw=(materials.directory(self.s)/meta['id']).read_bytes()
      if hashlib.sha256(raw).hexdigest()!=meta['payload_sha256']:raise ValueError()
      doc=json.loads(raw);content=doc['content'];previews=doc.get('previews',[])
     else:
      # Plain messages have no mutable URL rendering to reconstruct.
      if content.get('org.yitiao.forward_snapshot'):
       frozen=content['org.yitiao.forward_snapshot'];content=frozen['content'];previews=frozen.get('previews',[])
      else:previews=[]
     messages.append(m|{'content':content,'previews':previews})
    raw=archive.canonical(recipe['header']|{'messages':messages}).decode()
    if hashlib.sha256(raw.encode()).hexdigest()==h:
     with self.lock:self.job(h)['canonical']=raw
     return self.result(h,raw)
   except (HTTPException,KeyError,ValueError,OSError):pass
  return None
 def result(self,h,raw):return {'hash':h,'canonical':raw,'payload':json.loads(raw),'url':'https://chat.yitiao.org/records/'+h,'recovered':True,'files':list(json.loads(capability(self.s,h)['recipe']).get('files',{}).values())}
 def exchange(self,who,data):
  from .history import current_rooms
  if not isinstance(data,dict) or not isinstance(data.get('offers',[]),list) or len(data.get('offers',[]))>7:fail(400,'记录补齐请求无效')
  with connect(self.s.db) as c:native=c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=?',(who['subject'],who['device'])).fetchone()
  if not native:fail(403,'记录互助仅供已登录 App 使用')
  rooms=current_rooms(self.s,who)
  with self.lock:
   self.prune()
   for offer in data.get('offers',[]):
    if not isinstance(offer,dict):fail(400,'记录数据格式无效')
    h=offer.get('hash');cap=capability(self.s,h)
    if cap['room'] not in rooms:fail(403,'只能提供当前所在源会话的记录')
    job=self.jobs.get(h)
    if not job:continue
    raw=offer.get('canonical')
    if not isinstance(raw,str) or len(raw.encode())>512000 or hashlib.sha256(raw.encode()).hexdigest()!=h:fail(409,'记录未通过原发布哈希校验')
    # A matching original record is the capability proof. Only files named by
    # selected messages may be offered; descriptors must match saved receipts.
    payload=json.loads(raw)
    recipe=json.loads(cap['recipe'])
    if [m.get('id') for m in payload.get('messages',[])]!=[m['id'] for m in recipe['messages']]:fail(409,'记录选择不匹配')
    job['canonical']=raw
    frows=offer.get('files',[])
    if not isinstance(frows,list) or len(frows)>100:fail(400,'记录附件列表无效')
    for f in frows:
     fid=f.get('id') if isinstance(f,dict) else None
     if fid not in job['wants']:continue
     expected=recipe.get('files',{}).get(fid)
     if not expected:continue
     try:body=base64.b64decode(f['data'],validate=True)
     except (ValueError,KeyError,TypeError):fail(400,'附件数据无效')
     if len(body)>100000000 or hashlib.sha256(body).hexdigest()!=expected['sha256'] or len(body)!=int(expected['bytes']):fail(409,'记录附件校验失败')
     if sum(len(v) for j in self.jobs.values() for v in j['files'].values())+len(body)>256*1024*1024:fail(429,'记录中转缓存已满')
     job['files'][fid]=body
   asked=[]
   for h,j in self.jobs.items():
    cap=capability(self.s,h)
    if cap['room'] in rooms and (j['canonical'] is None or any(fid not in j['files'] for fid in j['wants'])):
     asked.append({'hash':h,'room':cap['room'],'ids':[m['id'] for m in json.loads(cap['recipe'])['messages']],'recipe':json.loads(cap['recipe']),'files':list(j['wants']-j['files'].keys())})
   return {'asked':asked[:7]}

def install(app,s,session):
 recovery=Recovery(app,s);app.state.record_recovery=recovery
 @app.post('/api/records/exchange')
 async def exchange(request:Request):
  from .day_packets import read_body
  who=session(request);return recovery.exchange(who,await read_body(request))
