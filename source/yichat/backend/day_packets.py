"""GMT complete-day receipts and bounded verified device recovery, protocol v2."""
import asyncio,base64,gzip,hashlib,json,re,secrets,time,threading,functools
from datetime import date,datetime,timezone,timedelta
from fastapi import Request,HTTPException
from . import archive,materials
from .db import connect,transaction
from .security import fail
FORMAT='yichat-day-packet-v2'
TYPES=('m.room.message','m.room.redaction')
SCHEMA='''CREATE TABLE IF NOT EXISTS packet_day_roots(room TEXT NOT NULL,day TEXT NOT NULL,sha256 TEXT NOT NULL,count INTEGER NOT NULL,sealed_ms INTEGER NOT NULL,PRIMARY KEY(room,day));
CREATE TABLE IF NOT EXISTS packet_rooms(room TEXT PRIMARY KEY,started_day TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS packet_protocol_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);'''
MAX_BYTES=256*1024*1024

def migrate(s):
 with connect(s.db) as c:c.executescript(SCHEMA)
def digest(value):return hashlib.sha256(archive.canonical(value)).hexdigest()
def immutable_ms(day):return archive.day_bounds(day)[1]+300000
def day_range(start,end):
 try:
  a=date.fromisoformat(start);b=date.fromisoformat(end)
  if str(a)!=start or str(b)!=end or not 1<=(b-a).days+1<=7:raise ValueError()
 except (ValueError,TypeError):fail(400,'请选择连续1至7个 GMT 日期')
 return [(a+timedelta(days=i)).isoformat() for i in range((b-a).days+1)]
def header(room,day):return {'day':day,'format':FORMAT,'immutable_at':str(immutable_ms(day)),'room_id':room,'version':'2'}
def access(s,who,room,day):
 start,end=archive.day_bounds(day)
 with archive.connect(s.archive_reader) as pg:
  if archive.allowed(pg,room,who['mxid'],start,end):return
  # A room created today has no hidden pre-creation messages. Mid-day joins
  # after existing conversation messages cannot gain complete-day access.
  joined=pg.execute("SELECT received_ms FROM memberships WHERE room=%s AND mxid=%s AND membership='join' AND received_ms>=%s AND received_ms<%s ORDER BY sequence LIMIT 1",(room,who['mxid'],start,end)).fetchone()
  earlier=pg.execute("SELECT 1 FROM events WHERE room=%s AND type=ANY(%s) AND received_ms<%s LIMIT 1",(room,list(TYPES),joined['received_ms'] if joined else end)).fetchone()
  if joined:
   with connect(s.db) as c:earlier=earlier or c.execute('SELECT 1 FROM history_receipts WHERE room=? AND type IN (?,?) AND received_ms<? LIMIT 1',(room,*TYPES,joined['received_ms'])).fetchone()
  if joined and not earlier and archive.allowed(pg,room,who['mxid'],joined['received_ms'],end):return
 fail(403,'没有此完整 GMT 日期的历史访问权限')
def file_projection(rows):
 return sorted([{'id':str(r['id']),'bytes':str(r['bytes']),'sha256':str(r['sha256'])} for r in rows],key=lambda r:r['id'])
def project_entry(entry,verify_files=True):
 try:
  if not isinstance(entry,dict) or type(entry.get('redacted',False))!=bool:raise ValueError('消息形状无效')
  raw=entry['raw'];event=json.loads(raw);eid=entry['event_id'];room=entry['room_id']
  if not isinstance(raw,str) or not raw.endswith('\n') or event['event_id']!=eid or event['room_id']!=room or event['sender']!=entry['sender'] or event['type']!=entry['type']:raise ValueError('事件身份不匹配')
  for n in ('received_ms','sequence'):
   if not re.fullmatch(r'0|[1-9][0-9]{0,18}',str(entry[n])):raise ValueError('时间或序号无效')
  raw_sha=hashlib.sha256(raw.encode()).hexdigest()
  frows=entry.get('files',[]);fproject=file_projection(frows)
  if len({r['id'] for r in fproject})!=len(fproject):raise ValueError('重复附件')
  for row in frows:
   if not re.fullmatch('[a-f0-9]{48}',row['id']) or not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise ValueError('附件摘要无效')
   if verify_files:
    data=base64.b64decode(row['data'],validate=True)
    if len(data)!=int(row['bytes']) or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('附件内容不匹配')
  material=entry.get('material');material_sha=None
  if material is not None:
   if any(str(material.get(k,''))!=str(v) for k,v in [('event_id',eid),('room_id',room),('sender',entry['sender']),('shared_at',entry['received_ms'])]) or material.get('finalized') is not True:raise ValueError('原始消息认证身份不匹配')
   canonical=material['canonical'];payload_hash=hashlib.sha256(canonical.encode()).hexdigest();payload=json.loads(canonical);ref=event['content'][materials.KEY]
   if material.get('id')!=ref.get('id') or material.get('kind')!=payload.get('kind') or ref.get('kind')!=payload.get('kind') or file_projection(material.get('files',[]))!=fproject or payload_hash!=ref['sha256'] or material['payload_sha256']!=payload_hash or file_projection(payload.get('files',[]))!=fproject:raise ValueError('原始消息内容不匹配')
   source={'event':eid,'room':room,'sender':entry['sender'],'received_ms':int(entry['received_ms'])}
   material_sha=materials.final_hash(source,payload_hash,fproject)
   if material.get('sha256')!=material_sha:raise ValueError('原始消息时间摘要不匹配')
  elif event.get('content',{}).get(materials.KEY):raise ValueError('缺少原始消息文件')
  return {'event_id':eid,'redacted':bool(entry.get('redacted',False)),'files':fproject,'material_sha256':material_sha,'raw_sha256':raw_sha,'received_ms':str(entry['received_ms']),'room_id':room,'sender':entry['sender'],'sequence':str(entry['sequence']),'type':entry['type']}
 except (KeyError,TypeError,ValueError,OverflowError) as e:raise ValueError(str(e)) from None

def packet_hash(packet,verify_files=True):
 room=packet['room_id'];day=packet['day'];start,end=archive.day_bounds(day);entries=packet['entries']
 if not isinstance(entries,list) or len(entries)>100000:raise ValueError('消息条数超限')
 projected=[];seen=set();previous=-1
 for entry in entries:
  if not isinstance(entry,dict):raise ValueError('消息必须是对象')
  if entry.get('room_id')!=room or not start<=int(entry.get('received_ms',-1))<end or entry.get('event_id') in seen or int(entry.get('sequence',-1))<=previous:raise ValueError('日期、会话、顺序或消息ID不匹配')
  seen.add(entry['event_id']);previous=int(entry['sequence']);projected.append(project_entry(entry,verify_files))
 return digest(header(room,day)|{'entries':projected})

def build(s,room,day,include_files=False,root_only=False):
 start,end=archive.day_bounds(day)
 with connect(s.db) as c:
  removed={r['event'] for r in c.execute('SELECT event FROM protocol_removed_events WHERE room=?',(room,))}
  expected=c.execute('SELECT event_id FROM history_receipts WHERE room=? AND received_ms>=? AND received_ms<? AND type IN (?,?)',(room,start,end,*TYPES)).fetchall()
 with archive.connect(s.archive_reader) as pg:
  rows=pg.execute('SELECT * FROM events WHERE room=%s AND day=%s AND type=ANY(%s) ORDER BY sequence,event_id',(room,date.fromisoformat(day),list(TYPES))).fetchall()
 rows=[r for r in rows if r['event_id'] not in removed]
 if {r['event_id'] for r in rows}!={r['event_id'] for r in expected if r['event_id'] not in removed}:fail(410,'服务器已经清理该日原始消息，需要在线 App 提供完整日期数据')
 entries=[]
 for row in rows:
  raw=bytes(row['body']).decode();event=json.loads(raw);entry={'event_id':row['event_id'],'room_id':room,'sender':row['sender'],'type':row['type'],'sequence':str(row['sequence']),'received_ms':str(row['received_ms']),'raw':raw,'files':[]}
  with connect(s.db) as c:entry['redacted']=bool(c.execute('SELECT 1 FROM history_receipts WHERE room=? AND redacts=? AND received_ms<?',(room,row['event_id'],immutable_ms(day))).fetchone())
  content=event.get('content',{});ref=content.get(materials.KEY)
  if ref:
   if root_only:
    meta=materials.saved_projection(s,row['event_id']);entry['_saved_material_sha256']=meta['sha256']
   else:
    meta=materials.state(s,row['event_id']);canonical=(materials.directory(s)/meta['id']).read_text();entry['material']=meta|{'canonical':canonical}
   entry['files']=[dict(f) for f in meta['files']]
  else:entry['files']=materials.files(s,content)
  if include_files:
   for f in entry['files']:
    data=(s.objects/f['id']).read_bytes()
    if hashlib.sha256(data).hexdigest()!=f['sha256']:fail(503,'服务器附件校验失败')
    f['data']=base64.b64encode(data).decode()
  if root_only:entry['_saved_raw_sha256']=row['body_sha256']
  entries.append(entry)
 packet=header(room,day)|{'entries':entries,'complete':True}
 if root_only:
  projected=[{k:e[k] for k in ('event_id','room_id','sender','type','sequence','received_ms','redacted')}|{'files':file_projection(e['files']),'raw_sha256':e['_saved_raw_sha256'],'material_sha256':e.get('_saved_material_sha256')} for e in entries]
  packet['sha256']=digest(header(room,day)|{'entries':projected})
 else:packet['sha256']=packet_hash(packet,verify_files=include_files)
 packet['count']=len(entries)
 return packet

def trusted(s,who,room,day):
 access(s,who,room,day)
 with connect(s.db) as c:r=c.execute('SELECT * FROM packet_day_roots WHERE room=? AND day=?',(room,day)).fetchone()
 if not r:fail(409,'此 GMT 日期尚未在00:05完成不可撤回封存')
 return dict(r)

def seal(s,room,day,fence,now_ms):
 if now_ms<immutable_ms(day):return False
 with connect(s.db) as c:
  if c.execute('SELECT 1 FROM packet_day_roots WHERE room=? AND day=?',(room,day)).fetchone():return True
 with archive.connect(s.archive_reader) as pg:
  cp=pg.execute("SELECT * FROM checkpoints WHERE source='synapse'").fetchone()
  if not cp or not cp['healthy'] or cp['sequence']<fence or cp['observed_ms']<immutable_ms(day) or now_ms-cp['observed_ms']>120000:return False
 packet=build(s,room,day,root_only=True)
 with transaction(s.db) as c:
  c.execute('INSERT OR IGNORE INTO packet_day_roots VALUES(?,?,?,?,?)',(room,day,packet['sha256'],packet['count'],immutable_ms(day)))
  saved=c.execute('SELECT sha256 FROM packet_day_roots WHERE room=? AND day=?',(room,day)).fetchone()
  if saved[0]!=packet['sha256']:raise RuntimeError('GMT day changed after irrevocable sealing')
 return True

async def tick(app):
 s=app.state.settings
 from .protocol_retention import ready
 if not ready(s):return
 now=int(time.time()*1000);failed=getattr(app.state,'packet_seal_failures',None)
 if failed is None:failed=app.state.packet_seal_failures={}
 today=archive.utcday(now);last=today-timedelta(days=1)
 if now<immutable_ms(str(last)):last-=timedelta(days=1)
 with connect(s.db) as c:rooms=[r['room'] for r in c.execute('SELECT room FROM rooms')]
 with archive.connect(s.archive_reader) as pg:
  firsts={r['room']:min(r['first_message'] or int(time.time()*1000),max(r['first'],int(time.time()*1000)-7*86400000)) for r in pg.execute("SELECT room,min(received_ms) first,min(received_ms) FILTER(WHERE type IN ('m.room.message','m.room.redaction')) first_message FROM events WHERE room=ANY(%s) GROUP BY room",(rooms,)).fetchall()}
 with transaction(s.db) as c:
  for room in rooms:
   first=archive.utcday(firsts.get(room,now))
   c.execute('INSERT OR IGNORE INTO packet_rooms VALUES(?,?)',(room,str(first)))
  jobs=[]
  for row in c.execute('SELECT * FROM packet_rooms'):
   d=date.fromisoformat(row['started_day'])
   while d<=last and len(jobs)<32:
    if failed.get((row['room'],str(d)),0)<=now and not c.execute('SELECT 1 FROM packet_day_roots WHERE room=? AND day=?',(row['room'],str(d))).fetchone():jobs.append((row['room'],str(d)))
    d+=timedelta(days=1)
 if jobs:
  fence=await app.state.matrix.fence()
  for room,day in jobs:
   try:
    await asyncio.to_thread(seal,s,room,day,fence,now);failed.pop((room,day),None)
   except Exception as e:
    failed[(room,day)]=now+300000
    __import__('logging').getLogger('yichat.packets').warning('GMT day not sealed: %s %s (%s)',room,day,getattr(e,'status_code',type(e).__name__))
 await asyncio.to_thread(materials.cleanup,s,now,True)

def preview_rows(packet):
 rows=packet.get('entries',[])
 return [r for r in rows[:1000] if isinstance(r,dict)] if isinstance(rows,list) else []

def safe_content(row):
 try:return json.loads(row.get('raw','{}')).get('content',{})
 except (ValueError,TypeError,AttributeError):return {'body':'原始消息数据损坏'}

def verify(s,who,data):
 if not isinstance(data,dict) or set(data)!={'room','start','end','days'}:fail(400,'需要 room、start、end 和完整日期数据 days')
 days=day_range(data['start'],data['end']);rows=data['days'];room=data['room']
 if not isinstance(room,str) or not isinstance(rows,list) or len(rows)!=len(days):fail(400,'完整日期数据不匹配')
 results=[]
 for day,packet in zip(days,rows):
  if not isinstance(packet,dict) or packet.get('room_id')!=room or packet.get('day')!=day:fail(400,'必须上传同会话连续整天数据，包括空日期')
  saved=trusted(s,who,room,day)
  try:actual=packet_hash(packet);valid=actual==saved['sha256'] and len(packet['entries'])==saved['count'] and packet.get('complete') is True
  except (ValueError,TypeError,KeyError):actual=None;valid=False
  results.append({'day':day,'room_id':room,'state':'ok' if valid else 'mismatch','sha256':saved['sha256'],'actual_sha256':actual,'count':saved['count'],'previews':[{'event_id':r.get('event_id'),'sender':r.get('sender'),'received_ms':r.get('received_ms'),'trusted':valid,'content':safe_content(r)} for r in preview_rows(packet)]})
 return {'format':FORMAT,'room':room,'days':results}

def relay_locked(method):
 @functools.wraps(method)
 def call(self,*args,**kwargs):
  with self.lock:return method(self,*args,**kwargs)
 return call

class Relay:
 def __init__(self,s):self.s=s;self.jobs={};self.online={};self.lock=threading.RLock()
 @relay_locked
 def prune(self):
  now=time.monotonic();self.jobs={k:v for k,v in self.jobs.items() if v['expires']>now};self.online={k:v for k,v in self.online.items() if v['at']>now-45}
 def native(self,who):
  with connect(self.s.db) as c:r=c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=?',(who['subject'],who['device'])).fetchone()
  if not r:fail(403,'完整日期设备互助仅供已登录 App 使用')
 @relay_locked
 def ask(self,who,room,day):
  self.native(who);saved=trusted(self.s,who,room,day)
  from .history import current_rooms
  if room not in current_rooms(self.s,who):fail(403,'必须仍在此会话才能请求成员补齐')
  self.prune()
  for key,row in self.jobs.items():
   if row['subject']==who['subject'] and row['device']==who['device'] and row['room']==room and row['day']==day:return key
  if len(self.jobs)>=128 or sum(r['subject']==who['subject'] for r in self.jobs.values())>=7:fail(429,'日期补齐队列已满')
  key=secrets.token_urlsafe(24);self.jobs[key]={'subject':who['subject'],'device':who['device'],'mxid':who['mxid'],'room':room,'day':day,'sha256':saved['sha256'],'expires':time.monotonic()+180,'packet':None}
  return key
 @relay_locked
 def exchange(self,who,data):
  if not isinstance(data,dict):fail(400,'需要完整日期请求对象')
  self.native(who);self.prune();from .history import current_rooms
  rooms=current_rooms(self.s,who);self.online[(who['subject'],who['device'])]={'at':time.monotonic(),'rooms':rooms}
  offers=data.get('offers',[]);wants=data.get('requests',[])
  if not isinstance(offers,list) or len(offers)>7 or not isinstance(wants,list) or len(wants)>7:fail(400,'每次最多处理7个完整日期')
  for offer in offers:
   if not isinstance(offer,dict) or not isinstance(offer.get('request'),str):fail(400,'设备数据格式无效')
   job=self.jobs.get(offer.get('request'))
   if not job or job['room'] not in rooms or (who['subject'],who['device'])==(job['subject'],job['device']):continue
   access(self.s,who,job['room'],job['day']);packet=offer.get('packet')
   if not isinstance(packet,dict) or packet.get('room_id')!=job['room'] or packet.get('day')!=job['day']:fail(400,'设备提供的数据与请求不匹配')
   saved=trusted(self.s,who,job['room'],job['day'])
   try:valid=packet_hash(packet)==saved['sha256'] and len(packet['entries'])==saved['count'] and packet.get('complete') is True
   except (ValueError,TypeError,KeyError):valid=False
   if not valid:fail(409,'提供设备的完整日期数据未通过服务器校验')
   size=len(archive.canonical(packet))
   if size>256*1024*1024 or sum(r.get('bytes',0) for r in self.jobs.values() if r is not job)+size>256*1024*1024:fail(429,'成员数据中转暂存已满，请稍后重试')
   job['packet']=packet|{'sha256':saved['sha256'],'count':saved['count'],'complete':True};job['bytes']=size
  asked=[];fulfilled=[]
  for key,job in self.jobs.items():
   if job['room'] not in rooms:continue
   if key in wants and (job['subject'],job['device'])==(who['subject'],who['device']) and job['packet'] is not None:
    trusted(self.s,who,job['room'],job['day']);fulfilled.append({'request':key,'packet':job['packet']})
   elif (job['subject'],job['device'])!=(who['subject'],who['device']) and job['packet'] is None:
    try:trusted(self.s,who,job['room'],job['day'])
    except HTTPException:continue
    asked.append({'request':key,'room':job['room'],'day':job['day'],'sha256':job['sha256']})
  return {'asked':asked[:7],'fulfilled':fulfilled[:7]}

async def read_body(request):
 body=await request.body()
 if len(body)>128*1024*1024:fail(413,'校验数据包超过128MB，请分开日期处理')
 if request.headers.get('content-encoding')=='gzip':
  import io
  try:
   with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:body=stream.read(MAX_BYTES+1)
  except (OSError,EOFError,__import__('zlib').error):fail(400,'压缩数据包损坏')
 if len(body)>MAX_BYTES:fail(413,'解压数据超过256MB')
 try:return json.loads(body)
 except (ValueError,UnicodeError):fail(400,'数据包格式无效')

def install(app,s,session):
 relay=Relay(s);app.state.day_relay=relay;slots=asyncio.Semaphore(2)
 @app.post('/api/packets/verify')
 async def verify_route(request:Request):
  who=session(request)
  async with slots:return await asyncio.to_thread(verify,s,who,await read_body(request))
 @app.post('/api/packets/check')
 async def check(request:Request):
  who=session(request);data=await request.json()
  if not isinstance(data,dict) or not isinstance(data.get('room'),str) or not isinstance(data.get('days'),list):fail(400,'日期校验请求格式无效')
  days=day_range(data.get('start'),data.get('end'));rows=data['days']
  if len(rows)!=len(days):fail(400,'需要连续完整日期，包括没有消息的日期')
  result=[]
  for day,row in zip(days,rows):
   if not isinstance(row,dict) or row.get('day')!=day:fail(400,'日期必须连续')
   saved=trusted(s,who,data.get('room'),day)
   good=row.get('complete') is True and row.get('sha256')==saved['sha256'] and row.get('count')==saved['count']
   result.append(saved|{'state':'ok' if good else 'mismatch','download':None if good else '/api/packets/day?room='+__import__('urllib.parse',fromlist=['quote']).quote(data['room'],safe='')+'&day='+day})
  return {'days':result}
 @app.get('/api/packets/day')
 async def get_day(request:Request,room:str,day:str,include_files:bool=False):
  who=session(request);saved=trusted(s,who,room,day)
  try:packet=await asyncio.to_thread(build,s,room,day,include_files)
  except HTTPException as e:
   if e.status_code!=410:raise
   return {'state':'peer_required','request':relay.ask(who,room,day),'sha256':saved['sha256']}
  if packet['sha256']!=saved['sha256']:fail(503,'服务器当日数据与封存摘要不符，已停止分发')
  return {'state':'available','packet':packet}
 @app.post('/api/packets/exchange')
 async def exchange(request:Request):
  who=session(request)
  async with slots:return await asyncio.to_thread(relay.exchange,who,await read_body(request))
 @app.get('/api/packets/days')
 async def list_days(request:Request,room:str,start:str,end:str):
  who=session(request);days=day_range(start,end);result=[]
  for day in days:
   try:result.append(trusted(s,who,room,day)|{'state':'sealed'})
   except HTTPException as e:
    if e.status_code not in (403,409):raise
    result.append({'room':room,'day':day,'state':'forbidden' if e.status_code==403 else 'pending'})
  return {'days':result,'timezone':'GMT','format':FORMAT}
