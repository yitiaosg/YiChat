"""Small, acknowledged file chunks. No chat event is created by this protocol."""
import asyncio,hashlib,os,re,secrets,time,logging
from fastapi import Request
from .db import connect,transaction
from .security import fail,reserve_rate,reserve_upload_slot,deny_retry
from . import archive

log=logging.getLogger('yichat.upload')
CHUNK_BYTES=131072
TTL=900
SCHEMA='''CREATE TABLE IF NOT EXISTS file_uploads(
 id TEXT PRIMARY KEY,subject TEXT NOT NULL,room TEXT NOT NULL,mxid TEXT NOT NULL,
 request_key TEXT NOT NULL,bytes INTEGER NOT NULL,offset INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'uploading',created REAL NOT NULL,expires REAL NOT NULL,completed REAL,
 UNIQUE(subject,request_key));
CREATE INDEX IF NOT EXISTS file_uploads_expiry ON file_uploads(state,expires);'''


def row_for(s,uid,subject):
    if not re.fullmatch('[a-f0-9]{48}',uid):fail(404,'上传任务不存在')
    with connect(s.db) as c:row=c.execute('SELECT * FROM file_uploads WHERE id=? AND subject=?',(uid,subject)).fetchone()
    if not row:fail(404,'上传任务不存在')
    if row['state']=='cancelled' or (row['state']=='uploading' and row['expires']<time.time()):fail(410,'此上传已取消或过期，请重新选择文件')
    return dict(row)


def public(s,row):
    result={'id':row['id'],'state':row['state'],'offset':row['offset'],'bytes':row['bytes'],'chunk_bytes':CHUNK_BYTES}
    if row['state']=='complete':
        with connect(s.db) as c:f=c.execute('SELECT * FROM files WHERE id=?',(row['id'],)).fetchone()
        if not f:fail(503,'文件确认记录暂不可用')
        result.update(url='mxc://'+s.server_name+'/'+row['id'],expires=f['expires'])
    return result


def activate_lease(c,s,row):
    now=time.time()
    c.execute('DELETE FROM upload_leases WHERE expires<?',(now,))
    if not c.execute('SELECT 1 FROM upload_leases WHERE id=?',(row['id'],)).fetchone():
        if c.execute('SELECT count(*) FROM upload_leases WHERE subject=?',(row['subject'],)).fetchone()[0]>=s.upload_concurrency:deny_retry(1,'上传连接数已达上限')
        c.execute('INSERT INTO upload_leases VALUES(?,?,?)',(row['id'],row['subject'],now+60))
    else:c.execute('UPDATE upload_leases SET expires=? WHERE id=?',(now+60,row['id']))


def append(s,row,offset,data):
    # SQLite serializes appends across workers. Bytes are durable before the ACK;
    # an interrupted pre-commit append is truncated back to the committed offset.
    with transaction(s.db) as c:
        current=c.execute('SELECT * FROM file_uploads WHERE id=?',(row['id'],)).fetchone()
        if current['state']!='uploading':fail(409,{'code':'YICHAT_UPLOAD_STATE','error':'文件正在确认或已完成'})
        if offset>current['offset']:fail(409,{'code':'YICHAT_UPLOAD_OFFSET','error':'上传进度需重新核对','offset':current['offset']})
        path=s.objects/(row['id']+'.part')
        if offset<current['offset']:
            with path.open('rb') as f:f.seek(offset);same=f.read(len(data))==data
            if offset+len(data)>current['offset'] or not same:fail(409,'重复分块内容不一致，未覆盖原文件')
            # Retransmitted bytes still consume ingress quota, not another full file.
            day=int(time.time()//86400);used=c.execute('SELECT bytes FROM upload_usage WHERE subject=? AND day=?',(row['subject'],day)).fetchone()
            if (used[0] if used else 0)+len(data)>s.daily_bytes:deny_retry(1,'今日上传额度已用完')
            c.execute('INSERT INTO upload_usage VALUES(?,?,?) ON CONFLICT(subject,day) DO UPDATE SET bytes=bytes+excluded.bytes',(row['subject'],day,len(data)))
            return dict(current)
        if not data or offset+len(data)>current['bytes']:fail(413,'分块超过文件声明长度')
        activate_lease(c,s,current)
        with path.open('r+b') as f:
            if os.fstat(f.fileno()).st_size<offset:fail(503,'已确认的分块缺失，停止上传')
            f.truncate(offset);f.seek(offset);f.write(data);f.flush();os.fsync(f.fileno())
        c.execute('UPDATE file_uploads SET offset=?,expires=? WHERE id=?',(offset+len(data),time.time()+TTL,row['id']))
        return dict(c.execute('SELECT * FROM file_uploads WHERE id=?',(row['id'],)).fetchone())


def finalize(s,uid):
    with transaction(s.db) as c:
        row=dict(c.execute('SELECT * FROM file_uploads WHERE id=?',(uid,)).fetchone())
        if row['state']=='complete':return row
        if row['state']=='cancelled':fail(410,'上传已取消')
        if row['offset']!=row['bytes']:fail(409,{'code':'YICHAT_UPLOAD_OFFSET','error':'文件尚未收齐','offset':row['offset']})
        c.execute("UPDATE file_uploads SET state='finalizing',completed=coalesce(completed,?),expires=? WHERE id=?",(time.time(),time.time()+60,uid))
        row=dict(c.execute('SELECT * FROM file_uploads WHERE id=?',(uid,)).fetchone())
    part=s.objects/(uid+'.part');target=s.objects/uid
    # Finalizing forbids appends/cancellation. Repeating this after a crash reuses
    # the same immutable bytes, receive time, receipt and file ID.
    try:source=part.open('rb')
    except FileNotFoundError:source=target.open('rb')
    digest=hashlib.sha256();total=0
    with source:
        while data:=source.read(1048576):digest.update(data);total+=len(data)
    if total!=row['bytes']:fail(503,'完整文件大小不一致，未发布附件')
    try:part.rename(target)
    except FileNotFoundError:
        if not target.is_file():raise
    directory=os.open(s.objects,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
    archive.record_attachment(s.archive_ingest,uid,row['room'],row['mxid'],int(row['completed']*1000),total,digest.hexdigest())
    expires=(int(row['completed']//86400)+31)*86400
    with transaction(s.db) as c:
        c.execute('INSERT OR IGNORE INTO files VALUES(?,?,?,?,?,?,0)',(uid,row['room'],row['subject'],total,row['completed'],expires))
        c.execute('INSERT OR IGNORE INTO file_hashes VALUES(?,?)',(uid,digest.hexdigest()))
        stored=c.execute('SELECT f.room,f.uploader,f.bytes,h.sha256 FROM files f JOIN file_hashes h USING(id) WHERE f.id=?',(uid,)).fetchone()
        if tuple(stored)!=(row['room'],row['subject'],total,digest.hexdigest()):raise RuntimeError('Upload receipt conflict')
        c.execute("UPDATE file_uploads SET state='complete',expires=? WHERE id=?",(expires,uid));c.execute('DELETE FROM upload_leases WHERE id=?',(uid,))
        return dict(c.execute('SELECT * FROM file_uploads WHERE id=?',(uid,)).fetchone())


def cancel(s,uid):
    with transaction(s.db) as c:
        row=c.execute('SELECT * FROM file_uploads WHERE id=?',(uid,)).fetchone()
        if not row or row['state']!='uploading':return
        c.execute("UPDATE file_uploads SET state='cancelled' WHERE id=?",(uid,));c.execute('DELETE FROM upload_leases WHERE id=?',(uid,))
    (s.objects/(uid+'.part')).unlink(missing_ok=True);archive.release_upload(s.archive_ingest,uid)


def reconcile(s):
    with connect(s.db) as c:rows=c.execute("SELECT id,state FROM file_uploads WHERE state IN ('uploading','finalizing') AND expires<? ORDER BY created LIMIT 5",(time.time(),)).fetchall()
    for row in rows:
        if row['state']=='uploading':cancel(s,row['id'])
        else:
            try:finalize(s,row['id'])
            except Exception:log.warning('File confirmation pending; retained draft')


def install(app,s,session,room_access,require_send):
    async def access(request,uid):
        who=session(request);row=row_for(s,uid,who['subject'])
        _,info=await room_access(request,row['room']);require_send(who,info)
        return who,row
    @app.post('/api/uploads')
    async def start(request:Request):
        data=await request.json()
        if not isinstance(data,dict) or set(data)!={'room','bytes','key'} or not isinstance(data['room'],str) or type(data['bytes'])!=int or not 0<=data['bytes']<=s.max_file or not isinstance(data['key'],str) or not re.fullmatch('[A-Za-z0-9_-]{16,100}',data['key']):fail(400,'上传参数无效或文件超过上限')
        who,info=await room_access(request,data['room']);require_send(who,info)
        with transaction(s.db) as c:
            old=c.execute('SELECT * FROM file_uploads WHERE subject=? AND request_key=?',(who['subject'],data['key'])).fetchone()
            if old:
                if (old['room'],old['bytes'])!=(data['room'],data['bytes']):fail(409,'上传请求对应另一份文件')
                return public(s,row_for(s,old['id'],who['subject']))
            uid=secrets.token_hex(24);now=time.time()
            reserve_upload_slot(c,s,who['subject'],uid,data['bytes'])
            c.execute('INSERT INTO file_uploads(id,subject,room,mxid,request_key,bytes,created,expires) VALUES(?,?,?,?,?,?,?,?)',(uid,who['subject'],data['room'],who['mxid'],data['key'],data['bytes'],now,now+TTL))
            (s.objects/(uid+'.part')).touch(exist_ok=False)
        try:await asyncio.to_thread(archive.reserve_upload,s.archive_ingest,uid)
        except Exception:
            cancel(s,uid);raise
        return public(s,row_for(s,uid,who['subject']))
    @app.get('/api/uploads/{uid}')
    async def status(request:Request,uid:str):
        _,row=await access(request,uid);return public(s,row)
    @app.put('/api/uploads/{uid}')
    async def chunk(request:Request,uid:str):
        who,row=await access(request,uid)
        try:offset=int(request.headers.get('x-upload-offset','-1'));size=int(request.headers.get('content-length','0'))
        except ValueError:fail(400,'上传分块头无效')
        if offset<0 or size<0 or size>CHUNK_BYTES:fail(413,'上传分块过大或进度无效')
        if size and offset+size>row['bytes']:fail(413,'分块超过文件声明长度')
        await asyncio.sleep(reserve_rate(s,who['subject'],'upload',size or min(CHUNK_BYTES,max(0,row['bytes']-offset)),s.upload_rate))
        data=await request.body()
        if len(data)>CHUNK_BYTES:fail(413,'上传分块超过上限')
        return public(s,await asyncio.to_thread(append,s,row,offset,data))
    @app.post('/api/uploads/{uid}/complete')
    async def complete(request:Request,uid:str):
        _,row=await access(request,uid)
        return public(s,await asyncio.to_thread(finalize,s,row['id']))
    @app.delete('/api/uploads/{uid}')
    async def remove(request:Request,uid:str):
        who=session(request);row=row_for(s,uid,who['subject']);await asyncio.to_thread(cancel,s,uid)
        return {'state':'cancelled' if row['state']=='uploading' else row['state']}
