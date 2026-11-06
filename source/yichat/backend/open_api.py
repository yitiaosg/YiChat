"""Developer API: web-managed API keys, read-only REST and a WebSocket stream.

Independent of every client: API keys are accepted only under /open/v1, and
browser/App sessions are never accepted there. Keys are created, listed and
deleted only from a browser session of the Web version. A key is shown once;
only its SHA-256 digest is stored.

Limits (shared by REST and WebSocket): 5 requests per rolling second per
account; at most 3 keys per account. There is no per-IP limit.
"""
import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from urllib.parse import quote
from fastapi import Request, WebSocket, WebSocketDisconnect, HTTPException
from . import archive, policy
from .db import connect, transaction
from .security import authenticate, digest, fail, request_limit

log=logging.getLogger('yichat.open_api')
SCHEMA='''CREATE TABLE IF NOT EXISTS api_keys(id TEXT PRIMARY KEY,subject TEXT NOT NULL,name TEXT NOT NULL,key_hash TEXT UNIQUE NOT NULL,prefix TEXT NOT NULL,created REAL NOT NULL,last_used REAL);
CREATE INDEX IF NOT EXISTS api_keys_subject ON api_keys(subject);'''

MAX_KEYS=3
RATE=5                    # per rolling second, per account
MAX_SPAN_MS=86400000      # one message query may span at most 24 hours
PAGE_DEFAULT=100
PAGE_MAX=200
PING_SECONDS=30           # idle server ping; the contract is "at least every 60 s"
POLL_SECONDS=1.0
RECHECK_SECONDS=10
MAX_CLIENT_FRAME=4096
KEY=re.compile(r'yck_[A-Za-z0-9_-]{43}\Z')
ROOM=re.compile(r'![A-Za-z0-9._=:-]{1,255}\Z')
NAME=re.compile(r'[^\x00-\x1f\x7f]{1,40}\Z')
MESSAGE_TYPES=('m.room.message','m.room.encrypted')
STREAM_TYPES=MESSAGE_TYPES+('m.room.redaction',)
SCOPE_ERROR={'code':'YICHAT_WEB_ONLY','error':'API Key 仅可在 YiChat 网页版中申请、查看和删除'}


def key_error(message='API Key 无效或已删除'):
    return HTTPException(401,{'errcode':'M_UNKNOWN_TOKEN','code':'YICHAT_API_KEY_INVALID','error':message})


def owner(s,token):
    """Resolve an API key to its account. Never accepts session tokens."""
    if not isinstance(token,str) or not KEY.fullmatch(token):raise key_error()
    with connect(s.db) as c:row=c.execute('SELECT * FROM api_keys WHERE key_hash=?',(digest(token),)).fetchone()
    if not row:raise key_error()
    with connect(s.accounts) as c:
        person=c.execute('SELECT a.username,u.* FROM account_ids a JOIN users u ON a.username=u.username WHERE a.uid=?',(row['subject'],)).fetchone()
    if not person or dict(person).get('purged_at'):raise key_error('API Key 所属账户已不可用')
    with connect(s.db) as c:identity=c.execute('SELECT mxid FROM identities WHERE subject=?',(row['subject'],)).fetchone()
    if not identity:raise key_error('API Key 所属账户尚未开通 YiChat')
    return {'key_id':row['id'],'subject':row['subject'],'username':person['username'],'mxid':identity['mxid'],'last_used':row['last_used']}


def touch(s,who):
    # Bounded write amplification: at most one last_used update per minute.
    now=time.time()
    if (who.get('last_used') or 0)<now-60:
        with transaction(s.db) as c:c.execute('UPDATE api_keys SET last_used=? WHERE id=?',(now,who['key_id']))


def current_rooms(s,mxid):
    with connect(s.ledger) as c:
        rooms={r['room'] for r in c.execute("SELECT room FROM yichat_memberships WHERE mxid=? AND membership='join' AND pending_event IS NULL",(mxid,))}
    with connect(s.db) as c:
        retired={r['room'] for r in c.execute('SELECT room FROM retired_rooms')}
    return rooms-retired


class Names:
    """Per-request mxid -> username cache. Unknown or purged users map to None."""
    def __init__(self,s):self.s=s;self.cache={}
    def __call__(self,mxid):
        if mxid not in self.cache:
            with connect(self.s.db) as c:row=c.execute('SELECT subject FROM identities WHERE mxid=?',(mxid,)).fetchone()
            name=None
            if row:
                try:name=policy.username(self.s,row['subject'])
                except HTTPException:name=None
            self.cache[mxid]=name
        return self.cache[mxid]


def message(s,row,names,redacted=False):
    """Public message shape shared by REST and WebSocket."""
    raw=bytes(row['body'])
    if hashlib.sha256(raw).hexdigest()!=row['body_sha256']:raise RuntimeError('Archive integrity mismatch')
    event=json.loads(raw)
    if event.get('event_id')!=row['event_id'] or event.get('room_id')!=row['room']:raise RuntimeError('Archive identity mismatch')
    content=event.get('content') if isinstance(event.get('content'),dict) else {}
    encrypted=row['type']=='m.room.encrypted'
    visible=not encrypted and not redacted
    return {
        'id':row['event_id'],
        'conversation_id':row['room'],
        'sequence':str(row['sequence']),
        'sender':{'mxid':row['sender'],'username':names(row['sender'])},
        'sent_at':event.get('origin_server_ts',row['received_ms']),
        'received_at':row['received_ms'],
        'msgtype':content.get('msgtype') if visible and isinstance(content.get('msgtype'),str) else None,
        'text':content.get('body') if visible and isinstance(content.get('body'),str) else None,
        'content':content if visible else None,
        'encrypted':encrypted,
        'redacted':redacted,
    }


def redacted_ids(s,room,ids):
    if not ids:return set()
    with connect(s.db) as c:
        return {r['redacts'] for r in c.execute('SELECT redacts FROM history_receipts WHERE room=? AND redacts IN ('+','.join('?' for _ in ids)+')',(room,*ids))}


def require_archive_ready(pg):
    cp=pg.execute("SELECT healthy,observed_ms FROM checkpoints WHERE source='synapse'").fetchone()
    if not cp or not cp['healthy'] or cp['observed_ms']<int(time.time()*1000)-120000:
        raise HTTPException(503,{'errcode':'M_UNKNOWN','code':'YICHAT_HISTORY_PENDING','error':'服务器消息存证正在同步，请稍后重试'},headers={'Retry-After':'2'})


def install(app,s):
    streams={}  # key_id -> open WebSocket count (single gateway worker)
    room_names={}  # room -> (expires, name)

    def web_session(request):
        # Web version only: the HttpOnly browser cookie must be present, any
        # Bearer must be that same session, and the session must not belong to
        # an Android/desktop App device (those authenticate with Bearer only).
        cookie=request.cookies.get('yc_session','')
        bearer=request.headers.get('authorization','')
        if not cookie or (bearer and bearer!='Bearer '+cookie):fail(403,SCOPE_ERROR)
        if request.headers.get('sec-fetch-site') not in (None,'same-origin'):fail(403,SCOPE_ERROR)
        who=authenticate(s,cookie)
        with connect(s.db) as c:
            if c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=? LIMIT 1',(who['subject'],who['device'])).fetchone():fail(403,SCOPE_ERROR)
        return who

    def listing(c,subject):
        return [{'id':r['id'],'name':r['name'],'prefix':r['prefix'],'created':r['created'],'last_used':r['last_used']}
                for r in c.execute('SELECT * FROM api_keys WHERE subject=? ORDER BY created',(subject,))]

    @app.get('/api/developer/keys')
    async def list_keys(request:Request):
        who=web_session(request)
        with connect(s.db) as c:keys=listing(c,who['subject'])
        return {'keys':keys,'limit':MAX_KEYS,'docs_url':s.api_docs_url}

    @app.post('/api/developer/keys')
    async def create_key(request:Request):
        who=web_session(request)
        try:data=await request.json()
        except ValueError:fail(400,'JSON 无效')
        name=data.get('name') if isinstance(data,dict) else None
        if not isinstance(data,dict) or set(data)-{'name'} or not isinstance(name,str) or not NAME.fullmatch(name.strip()):fail(400,'名称需为 1～40 个字符')
        token='yck_'+secrets.token_urlsafe(32);kid=secrets.token_hex(8);now=time.time()
        with transaction(s.db) as c:
            if c.execute('SELECT count(*) FROM api_keys WHERE subject=?',(who['subject'],)).fetchone()[0]>=MAX_KEYS:
                fail(409,{'code':'YICHAT_API_KEY_LIMIT','error':'每个账户最多 3 个 API Key，请先删除不再使用的 Key'})
            c.execute('INSERT INTO api_keys VALUES(?,?,?,?,?,?,NULL)',(kid,who['subject'],name.strip(),digest(token),token[:12],now))
        # The only response that ever contains the key.
        return {'id':kid,'name':name.strip(),'prefix':token[:12],'created':now,'key':token,'docs_url':s.api_docs_url}

    @app.delete('/api/developer/keys/{kid}')
    async def delete_key(request:Request,kid:str):
        who=web_session(request)
        with transaction(s.db) as c:
            if not c.execute('DELETE FROM api_keys WHERE id=? AND subject=?',(kid,who['subject'])).rowcount:fail(404,'API Key 不存在')
        # Open streams notice within RECHECK_SECONDS and close.
        return {'ok':True}

    def api_owner(request):
        auth=request.headers.get('authorization','')
        if not auth.startswith('Bearer '):raise key_error('需要 Authorization: Bearer <API Key>')
        who=owner(s,auth.removeprefix('Bearer '))
        request_limit(s,'api-user:'+who['subject'],limit=RATE)
        touch(s,who)
        return who

    async def room_name(info):
        if info['kind']!='group':return None
        hit=room_names.get(info['room'])
        if hit and hit[0]>time.monotonic():return hit[1]
        name=None
        try:
            result=await app.state.matrix.call('GET','/_synapse/admin/v1/rooms/'+quote(info['room'],safe=''))
            name=result.get('name') if isinstance(result.get('name'),str) else None
        except Exception:
            name=hit[1] if hit else None
        room_names[info['room']]=(time.monotonic()+300,name)
        return name

    @app.get('/open/v1/conversations')
    async def conversations(request:Request,archived:str='include'):
        who=api_owner(request)
        if archived not in ('include','exclude','only'):fail(400,'archived 只能是 include、exclude 或 only')
        names=Names(s);result=[]
        from . import conversations as prefs, direct, lifecycle
        for rid in sorted(current_rooms(s,who['mxid'])):
            try:info=policy.room(s,rid)
            except HTTPException:continue
            if not direct.visible(s,info,who['mxid']):continue
            with connect(s.db) as c:
                pref=prefs.entry_preference(c,who['subject'],rid)
                last=c.execute("SELECT max(received_ms) FROM history_receipts WHERE room=? AND type IN ('m.room.message','m.room.encrypted')",(rid,)).fetchone()[0]
                number=c.execute('SELECT number FROM group_settings WHERE room=?',(rid,)).fetchone() if info['kind']=='group' else None
            if pref['deleted'] or (archived=='exclude' and pref['archived']) or (archived=='only' and not pref['archived']):continue
            item={'id':rid,'type':info['kind'],'name':None,'archived':bool(pref['archived']),'pinned':bool(pref['pinned']),
                  'muted':bool(pref['muted']),'encrypted':info.get('encryption')=='legacy_encrypted','last_message_at':last}
            if info['kind']=='direct':
                peer=info['peer'] if who['mxid']==info['owner'] else info['owner']
                item['peer']={'mxid':peer,'username':names(peer),'deleted':bool(lifecycle.purged(s,peer))}
                item['name']=item['peer']['username']
            else:
                item['name']=await room_name(info);item['group_number']=number['number'] if number else None
            result.append(item)
        return {'conversations':result}

    @app.get('/open/v1/conversations/{room}/messages')
    async def messages(request:Request,room:str,start:int,end:int,after:str='',limit:int=PAGE_DEFAULT):
        who=api_owner(request)
        if not ROOM.fullmatch(room):fail(400,'会话 ID 无效')
        if start<0 or end<=start:fail(400,'需要 0 ≤ start < end（毫秒时间戳）')
        if end-start>MAX_SPAN_MS:fail(400,{'code':'YICHAT_RANGE_TOO_LONG','error':'一次查询最多横跨 24 小时（end - start ≤ 86400000）'})
        if not 1<=limit<=PAGE_MAX:fail(400,'limit 需为 1～200')
        if after and not re.fullmatch(r'[0-9]{1,18}',after):fail(400,'after 游标无效')
        if room not in current_rooms(s,who['mxid']):fail(403,{'code':'YICHAT_NOT_MEMBER','error':'你当前不是此会话的成员'})
        names=Names(s)
        def read():
            with archive.connect(s.archive_reader) as pg:
                require_archive_ready(pg)
                rows=pg.execute('SELECT event_id,room,sequence,received_ms,sender,type,body,body_sha256 FROM events WHERE room=%s AND received_ms>=%s AND received_ms<%s AND type = ANY(%s) AND sequence>%s ORDER BY sequence LIMIT %s',
                                (room,start,end,list(MESSAGE_TYPES),int(after or 0),limit+1)).fetchall()
                # Whole-window membership is the common case; otherwise check each event.
                whole=archive.allowed(pg,room,who['mxid'],start,end)
                visible=[r for r in rows[:limit] if whole or archive.allowed(pg,room,who['mxid'],r['received_ms'],r['received_ms']+1)]
            return rows,visible
        rows,visible=await asyncio.to_thread(read)
        gone=redacted_ids(s,room,[r['event_id'] for r in visible])
        more=len(rows)>limit
        return {'conversation_id':room,'start':start,'end':end,
                'messages':[message(s,r,names,r['event_id'] in gone) for r in visible],
                'next_after':str(rows[limit-1]['sequence']) if more else None}

    @app.websocket('/open/v1/stream')
    async def stream(ws:WebSocket):
        auth=ws.headers.get('authorization','')
        try:
            if not auth.startswith('Bearer '):raise key_error('需要 Authorization: Bearer <API Key>')
            who=owner(s,auth.removeprefix('Bearer '))
            request_limit(s,'api-user:'+who['subject'],limit=RATE)
        except HTTPException as e:
            # Accept first so the client can read the close code and reason.
            await ws.accept()
            code='YICHAT_RATE_LIMITED' if e.status_code==429 else 'YICHAT_API_KEY_INVALID'
            await ws.send_json({'type':'closing','code':code,'error':e.detail.get('error') if isinstance(e.detail,dict) else str(e.detail)})
            await ws.close(code=4429 if e.status_code==429 else 4401);return
        if streams.get(who['key_id'],0)>=1:
            await ws.accept()
            await ws.send_json({'type':'closing','code':'YICHAT_STREAM_BUSY','error':'此 API Key 已有一个 WebSocket 连接'})
            await ws.close(code=4409);return
        streams[who['key_id']]=streams.get(who['key_id'],0)+1
        try:
            touch(s,who)
            await ws.accept()
            await run_stream(ws,who)
        except WebSocketDisconnect:pass
        except RuntimeError:
            # Socket already closed by the reader, or an archive integrity
            # failure. Fixed message only: no keys, bodies or IDs in logs.
            log.warning('Developer API stream ended early')
            with __import__('contextlib').suppress(Exception):await ws.close(code=1011)
        finally:
            streams[who['key_id']]-=1
            if not streams[who['key_id']]:del streams[who['key_id']]

    async def run_stream(ws,who):
        def high():
            with archive.connect(s.archive_reader) as pg:return pg.execute('SELECT coalesce(max(sequence),0) n FROM events').fetchone()['n']
        cursor=await asyncio.to_thread(high)
        await ws.send_json({'type':'ready','server_time':int(time.time()*1000),'ping_interval':PING_SECONDS})
        closed=asyncio.Event();names=Names(s)

        async def reader():
            # Client frames are optional; each counts toward the account limit.
            try:
                while True:
                    frame=await ws.receive_text()
                    if len(frame)>MAX_CLIENT_FRAME:await ws.close(code=1009);break
                    try:request_limit(s,'api-user:'+who['subject'],limit=RATE)
                    except HTTPException:await ws.close(code=4429);break
                    try:data=json.loads(frame)
                    except ValueError:data=None
                    if isinstance(data,dict) and data.get('type')=='ping':await ws.send_json({'type':'pong','server_time':int(time.time()*1000)})
            except (WebSocketDisconnect,RuntimeError):pass
            finally:closed.set()

        task=asyncio.create_task(reader())
        last_sent=time.monotonic();checked=time.monotonic()
        try:
            while not closed.is_set():
                if time.monotonic()-checked>=RECHECK_SECONDS:
                    checked=time.monotonic()
                    try:owner(s,ws.headers.get('authorization','').removeprefix('Bearer '))
                    except HTTPException:
                        await ws.send_json({'type':'closing','code':'YICHAT_API_KEY_INVALID','error':'API Key 已删除或账户不可用'})
                        await ws.close(code=4401);return
                rooms=current_rooms(s,who['mxid'])
                def poll(after):
                    with archive.connect(s.archive_reader) as pg:
                        rows=pg.execute('SELECT event_id,room,sequence,received_ms,sender,type,body,body_sha256 FROM events WHERE sequence>%s AND room = ANY(%s) AND type = ANY(%s) ORDER BY sequence LIMIT 100',
                                        (after,sorted(rooms),list(STREAM_TYPES))).fetchall()
                        return [(r,archive.allowed(pg,r['room'],who['mxid'],r['received_ms'],r['received_ms']+1)) for r in rows]
                batch=await asyncio.to_thread(poll,cursor) if rooms else []
                for row,allowed in batch:
                    cursor=row['sequence']
                    if not allowed:continue
                    if row['type']=='m.room.redaction':
                        event=json.loads(bytes(row['body']))
                        target=(event.get('content') or {}).get('redacts') or event.get('redacts')
                        if isinstance(target,str):await ws.send_json({'type':'redaction','conversation_id':row['room'],'message_id':target,'sequence':str(row['sequence'])})
                    else:
                        await ws.send_json({'type':'message','conversation_id':row['room'],'message':message(s,row,names)})
                    last_sent=time.monotonic()
                if time.monotonic()-last_sent>=PING_SECONDS:
                    await ws.send_json({'type':'ping','server_time':int(time.time()*1000)});last_sent=time.monotonic()
                if not batch or len(batch)<100:
                    try:await asyncio.wait_for(closed.wait(),POLL_SECONDS)
                    except asyncio.TimeoutError:pass
        finally:
            task.cancel()
