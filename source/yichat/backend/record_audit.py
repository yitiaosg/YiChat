"""Date-bound audit of App message packets. Never accepts bodies or keys.

This digest specification is separate from the two unchanged ZIP archive specs.
The server derives it from accepted source ciphertext hashes, including recalls.
"""
import asyncio,hashlib,json,re
from datetime import date,timedelta
from fastapi import Request
from . import archive
from .db import connect,transaction
from .security import fail

FORMAT='yichat-record-digests-v1'
TYPES=('m.room.message','m.room.encrypted','m.room.redaction')
SCHEMA='''CREATE TABLE IF NOT EXISTS record_day_digests(room TEXT NOT NULL,day TEXT NOT NULL,count INTEGER NOT NULL,sha256 TEXT NOT NULL,archive_sha256 TEXT NOT NULL,PRIMARY KEY(room,day));'''

def header(room,day):return {'day':str(day),'format':FORMAT,'room_id':room,'version':1}
def receipt(row):
    event=json.loads(bytes(row['body']))
    return {'event_id':row['event_id'],'received_ms':row['received_ms'],'redacts':(event.get('content',{}).get('redacts') or event.get('redacts')) if row['type']=='m.room.redaction' else None,'room_id':row['room'],'sender':row['sender'],'sequence':row['sequence'],'sha256':row['body_sha256'],'type':row['type']}

def seal_digest(s,pg,room,day,archive_sha256):
    with connect(s.db) as c:old=c.execute('SELECT * FROM record_day_digests WHERE room=? AND day=?',(room,str(day))).fetchone()
    if old:
        if old['archive_sha256']!=archive_sha256:raise RuntimeError('Record digest archive binding changed')
        return dict(old)
    digest=hashlib.sha256(archive.canonical(header(room,day)));count=0
    with pg.cursor(name='record_digest_stream') as stream:
        stream.execute('SELECT * FROM events WHERE room=%s AND day=%s AND type=ANY(%s) ORDER BY sequence,event_id',(room,day,list(TYPES)))
        for row in stream:digest.update(archive.canonical(receipt(row)));count+=1
    actual=digest.hexdigest()
    with transaction(s.db) as c:
        c.execute('INSERT OR IGNORE INTO record_day_digests VALUES(?,?,?,?,?)',(room,str(day),count,actual,archive_sha256))
        saved=c.execute('SELECT * FROM record_day_digests WHERE room=? AND day=?',(room,str(day))).fetchone()
        if saved['sha256']!=actual or saved['count']!=count:raise RuntimeError('Daily record evidence changed')
    return dict(saved)

def trusted(s,who,room,day):
    archive.authorize(s.archive_reader,room,who['mxid'],day)
    summary=archive.summary(s.archive_reader,room,day,'events-manifest')
    if not summary:return {'state':'not_sealed','error':'此 UTC 日期尚未完整封存'}
    if summary['has_gaps']:return {'state':'quarantined','error':'此日期存证存在缺口，暂停核验'}
    with connect(s.db) as c:row=c.execute('SELECT * FROM record_day_digests WHERE room=? AND day=?',(room,str(day))).fetchone()
    if row:
        if row['archive_sha256']!=summary['sha256']:return {'state':'quarantined','error':'记录摘要与归档存证不一致，已停止核验'}
        return dict(row)|{'state':'ready'}
    if summary['cleaned_at']:return {'state':'unavailable','error':'旧日期未保留此核验规格，请使用原归档文件与可信摘要校验'}
    with archive.connect(s.archive_reader) as pg:
        pg.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        audits=pg.execute('SELECT cleaned_at,cleanup_started_at FROM archive_audit WHERE room=%s AND day=%s AND version=1',(room,day)).fetchall()
        if any(r['cleaned_at'] or r['cleanup_started_at'] for r in audits):return {'state':'unavailable','error':'旧记录实体已清理且没有此核验规格，请使用原归档文件校验'}
        return seal_digest(s,pg,room,day,summary['sha256'])|{'state':'ready'}

def verify(s,who,body):
    if not isinstance(body,dict) or set(body)!={'room','start','end','days'}:fail(400,'仅接受房间、连续日期和本地摘要，不接受正文或密钥')
    try:
        start=date.fromisoformat(body['start']);end=date.fromisoformat(body['end'])
        if str(start)!=body['start'] or str(end)!=body['end']:raise ValueError()
    except (ValueError,TypeError,KeyError):fail(400,'请选择有效的 UTC 日期')
    span=(end-start).days+1
    if not 1<=span<=5:fail(400,'请选择连续1至5日')
    rows=body['days'];room=body['room']
    if not isinstance(room,str) or not room.startswith('!') or len(room)>255 or not isinstance(rows,list) or len(rows)!=span:fail(400,'日期或房间不匹配')
    result=[]
    for index,row in enumerate(rows):
        day=start+timedelta(days=index)
        if not isinstance(row,dict) or set(row)!={'day','count','sha256','complete'} or row['day']!=str(day) or type(row['count'])!=int or row['count']<0 or type(row['complete'])!=bool or (row['sha256'] is not None and (not isinstance(row['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',row['sha256']))):fail(400,'只允许连续日期、数量和 SHA-256 摘要')
        try:saved=trusted(s,who,room,day)
        except PermissionError:saved={'state':'forbidden','error':'没有此完整 UTC 日期的历史权限'}
        status=saved['state']
        if status=='ready':status='incomplete' if not row['complete'] or row['sha256'] is None else 'ok' if row['count']==saved['count'] and row['sha256']==saved['sha256'] else 'mismatch'
        result.append({'day':str(day),'state':status,'server_count':saved.get('count'),'sha256':saved.get('sha256'),'error':saved.get('error')})
    return {'format':FORMAT,'room':room,'days':result}

def verify_files(s,who,body):
    if not isinstance(body,dict) or set(body)!={'room','start','end','days'}:fail(400,'仅接受房间、日期与文件摘要')
    try:
        start=date.fromisoformat(body['start']);end=date.fromisoformat(body['end'])
        if str(start)!=body['start'] or str(end)!=body['end']:raise ValueError()
    except (ValueError,TypeError,KeyError):fail(400,'请选择有效的 UTC 日期')
    span=(end-start).days+1;room=body['room'];rows=body['days']
    if not 1<=span<=5 or not isinstance(room,str) or not room.startswith('!') or len(room)>255 or not isinstance(rows,list) or len(rows)!=span:fail(400,'请选择同一会话的连续1至5日归档文件')
    result=[]
    for index,row in enumerate(rows):
        day=start+timedelta(days=index)
        if not isinstance(row,dict) or set(row)!={'day','spec','bytes','sha256'} or row['day']!=str(day) or row['spec'] not in archive.SPECS or type(row['bytes'])!=int or not 0<row['bytes']<=100000000 or not isinstance(row['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',row['sha256']):fail(400,'归档日期、规格或摘要不正确')
        try:
            archive.authorize(s.archive_reader,room,who['mxid'],day)
            saved=archive.summary(s.archive_reader,room,day,row['spec'])
            status='not_sealed' if not saved else 'quarantined' if saved['has_gaps'] else 'ok' if saved['sha256']==row['sha256'] and saved['bytes']==row['bytes'] else 'mismatch'
        except PermissionError:saved=None;status='forbidden'
        result.append({'day':str(day),'state':status,'sha256':saved['sha256'] if saved else None,'bytes':saved['bytes'] if saved else None})
    return {'format':'yichat-archive-v1','room':room,'days':result}

def install(app,s,session):
    slots=asyncio.Semaphore(2)
    @app.post('/api/records/verify')
    async def compare(request:Request):
        who=session(request);body=await request.json()
        async with slots:return await asyncio.to_thread(verify,s,who,body)

    @app.post('/api/records/verify-files')
    async def compare_files(request:Request):
        who=session(request);body=await request.json()
        async with slots:return await asyncio.to_thread(verify_files,s,who,body)
