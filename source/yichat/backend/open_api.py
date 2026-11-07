"""Developer API: web-managed API keys, read-only REST and a WebSocket stream.

Independent of every client: API keys are accepted only under /open/v1, and
browser/App sessions are never accepted there. Keys are created, listed and
deleted only from a browser session of the Web version. A key is shown once;
only its SHA-256 digest is stored.

Limits (shared by REST, uploads, downloads and WebSocket): 5 requests per
rolling second per account; at most 3 keys and 3 concurrent WebSocket
subscriptions per account. There is no per-IP limit.

Permissions: each key is either "read" (default) or "read_write", chosen
when it is created and changeable later from the Web version. Only
read_write keys may send; everything else is reading.

Recall: read_write keys can recall (redact) messages with the same rules as
the Web version: your own messages in private and group chats within 5
minutes; only the group owner may recall other members' messages; transfers,
red packets and gift cards cannot be recalled. The Synapse module remains
the authority for every rule.

Sending: each key gets its own Matrix device ("YiChat API · <name>"), created
on first use and removed when the key is deleted. Every message passes the
same gateway checks as the apps (membership, send policy, attachment
ownership) and is admitted by the Synapse module through a one-time grant for
exactly that content (backend/api_grants.py) instead of a live main-site
session.

Read receipts: a stream opened with ?conversation=<id> marks the newest
delivered message from other members as read (m.read via the key's device),
so senders see the double tick exactly as when the apps show the message.
Account-wide streams do not send receipts.
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
from . import archive, policy, previews, api_grants
from .notification_summary import URL, TRAILING
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
MAX_STREAMS=3             # concurrent WebSocket subscriptions per account
MAX_TEXT=2000             # same limit as the apps (Unicode code points)
FILE_ID=re.compile(r'[a-f0-9]{48}\Z')
MXC=re.compile(r'mxc://chat\.yitiao\.org/([a-f0-9]{48})\Z')
TXN=re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
FILENAME=re.compile(r'[^\x00-\x1f\x7f/\\]{1,200}\Z')
MIME=re.compile(r'[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}\Z')
IMAGE_TYPES=('image/png','image/jpeg','image/gif','image/webp','image/avif')
MEDIA={'m.image':('image','图片'),'m.file':('file','文件'),'m.audio':('audio','语音'),'m.video':('video','视频')}
SHARES={'card':'卡片','transaction':'YIDT 交易','doudizhu':'斗地主房间','doudizhu_record':'斗地主对局记录','game':'游戏记录'}
GIFT=re.compile(r'https://chat\.yitiao\.org/gifts/[a-f0-9]{32}')
NOT_MEMBER={'code':'YICHAT_NOT_MEMBER','error':'你当前不是此会话的成员'}
SCOPES=('read','read_write')
EVENT_ID=re.compile(r'\$[A-Za-z0-9._~:/+=-]{1,255}\Z')
RECALL_WINDOW_MS=300000  # mirrors the Synapse module (validate_redaction)
OWNER_RECALL_REASON='群主撤回成员消息'  # same reason as the Web version's owner recall
READ_ONLY={'code':'YICHAT_API_KEY_READ_ONLY','error':'此 API Key 只有读取权限；请在网页版的开发者 API 设置中改为“读写”'}


def migrate(c):
    columns={r[1] for r in c.execute('PRAGMA table_info(api_keys)')}
    for column in ('device','upstream'):
        if column not in columns:c.execute(f'ALTER TABLE api_keys ADD COLUMN {column} TEXT')
    # Keys created before permissions existed were read-only by design.
    if 'scope' not in columns:c.execute("ALTER TABLE api_keys ADD COLUMN scope TEXT NOT NULL DEFAULT 'read'")
    c.executescript(api_grants.SCHEMA)
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
    return {'key_id':row['id'],'key_name':row['name'],'subject':row['subject'],'username':person['username'],'mxid':identity['mxid'],
            'last_used':row['last_used'],'device':row['device'],'upstream':row['upstream'],'scope':row['scope']}


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


class Describer:
    """Plain-text view of a message for API clients.

    Transfers, red packets, receipts and in-site shares are URLs underneath;
    they become short text such as "[转账] 0.01 YIDT". Images and files show
    their name and expose an attachment that can be downloaded separately.
    """
    def __init__(self,s,fmt):self.s=s;self.fmt=fmt
    def order(self,room,oid):
        with connect(self.s.ledger) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='yichat_orders'").fetchone():return None
            # Same room only: a message cannot reveal another conversation's order.
            return c.execute('SELECT kind,title,principal,count,state FROM yichat_orders WHERE id=? AND room=?',(oid,room)).fetchone()
    def __call__(self,room,content):
        msgtype=content.get('msgtype');body=content.get('body') if isinstance(content.get('body'),str) else ''
        if msgtype in MEDIA:
            kind,label=MEDIA[msgtype];info=content.get('info') if isinstance(content.get('info'),dict) else {}
            name=body.strip() or label;match=MXC.fullmatch(str(content.get('url','')))
            attachment={'id':match[1],'name':name,'mimetype':info.get('mimetype') if isinstance(info.get('mimetype'),str) else None,
                        'size':info.get('size') if type(info.get('size'))==int else None,'download_url':'/open/v1/files/'+match[1]} if match else None
            return {'kind':kind,'text':'['+label+'] '+name,'attachment':attachment,'share':None}
        share=content.get('org.yitiao.share') if isinstance(content.get('org.yitiao.share'),dict) else {}
        business=content.get('org.yitiao.business') if isinstance(content.get('org.yitiao.business'),dict) else {}
        urls=[u.rstrip(TRAILING) for u in URL.findall(body)]
        if isinstance(share.get('url'),str):urls.insert(0,share['url'])
        parsed=[(u,p) for u,p in ((u,previews.parse(u)) for u in urls) if p]
        oid=business.get('id') if business.get('kind')=='order' and isinstance(business.get('id'),str) else next((p[1][0] for _,p in parsed if p[0]=='order'),None)
        if oid:
            receipt=share.get('type')=='receipt';order=self.order(room,oid)
            if order:
                amount=self.fmt(int(order['principal']))+' YIDT';title=(order['title'] or '').strip()
                if receipt:kind,text='receipt','[已收款] '+amount
                elif order['kind']=='group':kind,text='red_packet','[红包] '+amount+' · '+str(order['count'])+' 个'+(' · '+title if title else '')
                else:kind,text='transfer','[转账] '+amount+(' · '+title if title and title!='私聊转账' else '')
                detail={'type':'order','order_id':oid,'url':self.s.origin+'/orders/'+oid,'amount':self.fmt(int(order['principal'])),'currency':'YIDT','state':order['state']}
            else:
                kind,text='receipt' if receipt else 'transfer','[已收款]' if receipt else '[转账 / 红包]'
                detail={'type':'order','order_id':oid,'url':self.s.origin+'/orders/'+oid}
            return {'kind':kind,'text':text,'attachment':None,'share':detail}
        gift=next((u for u in urls if GIFT.fullmatch(u)),None)
        if gift or share.get('type')=='gift':
            return {'kind':'gift','text':'[赠卡]','attachment':None,'share':{'type':'gift','url':gift or share.get('url')}}
        for url,(kind,_) in parsed:
            if kind not in SHARES:continue
            try:meta=previews.metadata(self.s,url)
            except Exception:meta=None
            title=meta['title'] if meta and isinstance(meta.get('title'),str) else SHARES[kind]
            label='[分享] '+title
            return {'kind':'share','text':body.replace(url,label) if url in body else label,'attachment':None,'share':{'type':kind,'url':url,'title':title}}
        return {'kind':'text','text':body,'attachment':None,'share':None}


def message(s,row,names,redacted=False,describe=None):
    """Public message shape shared by REST and WebSocket."""
    raw=bytes(row['body'])
    if hashlib.sha256(raw).hexdigest()!=row['body_sha256']:raise RuntimeError('Archive integrity mismatch')
    event=json.loads(raw)
    if event.get('event_id')!=row['event_id'] or event.get('room_id')!=row['room']:raise RuntimeError('Archive identity mismatch')
    content=event.get('content') if isinstance(event.get('content'),dict) else {}
    encrypted=row['type']=='m.room.encrypted'
    visible=not encrypted and not redacted
    view=(describe or Describer(s,str))(row['room'],content) if visible else {'kind':'encrypted' if encrypted else 'redacted','text':None,'attachment':None,'share':None}
    return {
        'id':row['event_id'],
        'conversation_id':row['room'],
        'sequence':str(row['sequence']),
        'sender':{'mxid':row['sender'],'username':names(row['sender'])},
        'sent_at':event.get('origin_server_ts',row['received_ms']),
        'received_at':row['received_ms'],
        'kind':view['kind'],
        'text':view['text'],
        'attachment':view['attachment'],
        'share':view['share'],
        'msgtype':content.get('msgtype') if visible and isinstance(content.get('msgtype'),str) else None,
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
    streams={}  # account subject -> open WebSocket count (single gateway worker)
    device_locks={}  # key_id -> lock for first-use Matrix device creation
    def describer():return Describer(s,app.state.funds.y.fmt)
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
        return [{'id':r['id'],'name':r['name'],'prefix':r['prefix'],'created':r['created'],'last_used':r['last_used'],'scope':r['scope']}
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
        if not isinstance(data,dict) or set(data)-{'name','scope'} or not isinstance(name,str) or not NAME.fullmatch(name.strip()):fail(400,'名称需为 1～40 个字符')
        scope=data.get('scope','read')
        if scope not in SCOPES:fail(400,'权限只能是 read（读）或 read_write（读写）')
        token='yck_'+secrets.token_urlsafe(32);kid=secrets.token_hex(8);now=time.time()
        with transaction(s.db) as c:
            if c.execute('SELECT count(*) FROM api_keys WHERE subject=?',(who['subject'],)).fetchone()[0]>=MAX_KEYS:
                fail(409,{'code':'YICHAT_API_KEY_LIMIT','error':'每个账户最多 3 个 API Key，请先删除不再使用的 Key'})
            c.execute('INSERT INTO api_keys(id,subject,name,key_hash,prefix,created,scope) VALUES(?,?,?,?,?,?,?)',(kid,who['subject'],name.strip(),digest(token),token[:12],now,scope))
        # The only response that ever contains the key.
        return {'id':kid,'name':name.strip(),'prefix':token[:12],'created':now,'scope':scope,'key':token,'docs_url':s.api_docs_url}

    @app.patch('/api/developer/keys/{kid}')
    async def update_key(request:Request,kid:str):
        who=web_session(request)
        try:data=await request.json()
        except ValueError:fail(400,'JSON 无效')
        if not isinstance(data,dict) or set(data)!={'scope'} or data['scope'] not in SCOPES:fail(400,'只能修改权限：read（读）或 read_write（读写）')
        with transaction(s.db) as c:
            if not c.execute('UPDATE api_keys SET scope=? WHERE id=? AND subject=?',(data['scope'],kid,who['subject'])).rowcount:fail(404,'API Key 不存在')
            # Downgrading takes effect at once, including grants already issued.
            if data['scope']=='read':api_grants.revoke_key(c,kid)
            row=c.execute('SELECT * FROM api_keys WHERE id=?',(kid,)).fetchone()
        return {'id':row['id'],'name':row['name'],'prefix':row['prefix'],'created':row['created'],'last_used':row['last_used'],'scope':row['scope']}

    @app.delete('/api/developer/keys/{kid}')
    async def delete_key(request:Request,kid:str):
        who=web_session(request)
        with transaction(s.db) as c:
            row=c.execute('SELECT device FROM api_keys WHERE id=? AND subject=?',(kid,who['subject'])).fetchone()
            if not row:fail(404,'API Key 不存在')
            c.execute('DELETE FROM api_keys WHERE id=?',(kid,));api_grants.revoke_key(c,kid)
        device_locks.pop(kid,None)
        if row['device']:await drop_device(who['mxid'],row['device'])
        # Open streams notice within RECHECK_SECONDS and close.
        return {'ok':True}

    async def drop_device(mxid,device):
        # Logs the key's Matrix device out. Best effort: the token was only ever
        # stored (encrypted) in the deleted row, so nothing can use it anyway.
        try:await app.state.matrix.call('DELETE','/_synapse/admin/v2/users/'+quote(mxid,safe='')+'/devices/'+quote(device,safe=''))
        except Exception:log.warning('Developer API device removal pending')

    async def matrix_session(who):
        """The key's own Matrix device, created on first send."""
        if who.get('upstream'):return who
        async with device_locks.setdefault(who['key_id'],asyncio.Lock()):
            with connect(s.db) as c:row=c.execute('SELECT name,device,upstream FROM api_keys WHERE id=?',(who['key_id'],)).fetchone()
            if not row:raise key_error()
            if row['upstream']:return who|{'device':row['device'],'upstream':row['upstream']}
            with connect(s.db) as c:identity=c.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()
            if not identity:raise key_error('API Key 所属账户尚未开通 YiChat')
            cipher=app.state.cipher;password=cipher.decrypt(identity['password'].encode()).decode();m=app.state.matrix
            await m.provision(who['mxid'],password,who['username'])
            auth=await m.login(who['mxid'],password,device_name=('YiChat API · '+row['name'])[:60])
            upstream=cipher.encrypt(auth['access_token'].encode()).decode()
            with transaction(s.db) as c:
                stored=c.execute('UPDATE api_keys SET device=?,upstream=? WHERE id=? AND upstream IS NULL',(auth['device_id'],upstream,who['key_id'])).rowcount
            if not stored:
                await drop_device(who['mxid'],auth['device_id']);raise key_error()
            return who|{'device':auth['device_id'],'upstream':upstream}

    def writable(who,room):
        if who.get('scope')!='read_write':fail(403,READ_ONLY)
        if not ROOM.fullmatch(room):fail(400,'会话 ID 无效')
        if room not in current_rooms(s,who['mxid']):fail(403,NOT_MEMBER)
        info=policy.room(s,room)
        if info.get('encryption')!='plain':fail(403,{'code':'YICHAT_LEGACY_ROOM','error':'旧加密会话已停用，不能发送消息'})
        policy.require_send(s,info,who['mxid'])
        return info

    async def send_message(who,info,content,txn):
        room=info['room']
        policy.validate_event(s,info,'m.room.message',content,who['mxid'])
        who=await matrix_session(who)
        gid=api_grants.create(s,who['key_id'],room,who['mxid'],'m.room.message',content)
        path='/_matrix/client/v3/rooms/'+quote(room,safe='')+'/send/m.room.message/'+quote(txn,safe='')
        try:result=await app.state.matrix.call('PUT',path,app.state.matrix.token(who),content)
        except HTTPException as e:
            detail=e.detail if isinstance(e.detail,dict) else {}
            if e.status_code==401 and detail.get('errcode')=='M_UNKNOWN_TOKEN':
                # The key's device was logged out elsewhere; a retry creates a new one.
                with transaction(s.db) as c:c.execute('UPDATE api_keys SET device=NULL,upstream=NULL WHERE id=?',(who['key_id'],))
                raise HTTPException(503,{'code':'YICHAT_API_DEVICE_RESET','error':'API 设备已失效并已重置，请重试'},headers={'Retry-After':'1'})
            # The Synapse module reports its own YICHAT_* code (for example the
            # recall window); keep it so API clients can tell rules apart.
            code=detail.get('code') if isinstance(detail.get('code'),str) and detail.get('code').startswith('YICHAT_') and detail.get('code')!='YICHAT_POLICY_REJECTED' else 'YICHAT_SEND_REJECTED'
            raise HTTPException(e.status_code,{'errcode':detail.get('errcode','M_UNKNOWN'),'code':code,'error':detail.get('error') or '服务器拒绝了这条消息'},headers=e.headers)
        event_id=result.get('event_id')
        if not isinstance(event_id,str):fail(502,'服务器未返回消息 ID')
        api_grants.consume(s,gid,event_id)
        return event_id

    async def recall(who,info,target,reason):
        room=info['room']
        who=await matrix_session(who)
        gid=api_grants.create(s,who['key_id'],room,who['mxid'],'m.room.redaction',{'redacts':target})
        # Deterministic transaction ID: retrying the same recall with the same
        # key is idempotent at the Matrix layer.
        txn='api-recall-'+hashlib.sha256((who['key_id']+'|'+target).encode()).hexdigest()[:32]
        path='/_matrix/client/v3/rooms/'+quote(room,safe='')+'/redact/'+quote(target,safe='')+'/'+txn
        try:result=await app.state.matrix.call('PUT',path,app.state.matrix.token(who),{'reason':reason} if reason else {})
        except HTTPException as e:
            detail=e.detail if isinstance(e.detail,dict) else {}
            if e.status_code==401 and detail.get('errcode')=='M_UNKNOWN_TOKEN':
                with transaction(s.db) as c:c.execute('UPDATE api_keys SET device=NULL,upstream=NULL WHERE id=?',(who['key_id'],))
                raise HTTPException(503,{'code':'YICHAT_API_DEVICE_RESET','error':'API 设备已失效并已重置，请重试'},headers={'Retry-After':'1'})
            code=detail.get('code') if isinstance(detail.get('code'),str) and detail.get('code').startswith('YICHAT_') and detail.get('code')!='YICHAT_POLICY_REJECTED' else 'YICHAT_RECALL_REJECTED'
            raise HTTPException(e.status_code,{'errcode':detail.get('errcode','M_UNKNOWN'),'code':code,'error':detail.get('error') or '服务器拒绝了撤回'},headers=e.headers)
        event_id=result.get('event_id')
        if not isinstance(event_id,str):fail(502,'服务器未返回撤回事件 ID')
        api_grants.consume(s,gid,event_id)
        return event_id

    @app.delete('/open/v1/conversations/{room}/messages/{message_id}')
    async def recall_message(request:Request,room:str,message_id:str):
        who=api_owner(request)
        info=writable(who,room)
        if not EVENT_ID.fullmatch(message_id):fail(404,{'code':'YICHAT_MESSAGE_NOT_FOUND','error':'消息不存在'})
        with connect(s.db) as c:
            row=c.execute("SELECT sender,type,received_ms,business FROM history_receipts WHERE room=? AND event_id=?",(room,message_id)).fetchone()
            done=c.execute("SELECT event_id FROM history_receipts WHERE room=? AND redacts=? ORDER BY sequence LIMIT 1",(room,message_id)).fetchone()
        if not row or row['type'] not in MESSAGE_TYPES:fail(404,{'code':'YICHAT_MESSAGE_NOT_FOUND','error':'消息不存在或尚未同步'})
        if done:return {'conversation_id':room,'message_id':message_id,'event_id':done['event_id'],'already_recalled':True}
        own=row['sender']==who['mxid']
        if not own:
            # Same as the Web version: only the group owner recalls others' messages.
            if info['kind']!='group':fail(403,{'code':'YICHAT_RECALL_FORBIDDEN','error':'只能撤回自己的私聊消息'})
            from . import groups
            with connect(s.db) as c:
                try:groups.permission(c,room,who['mxid'],'redact',row['sender'])
                except HTTPException:fail(403,{'code':'YICHAT_RECALL_FORBIDDEN','error':'只有群主可以撤回其他成员的消息'})
        if row['business']:fail(403,{'code':'YICHAT_BUSINESS_IMMUTABLE','error':'转账、红包和赠卡消息不允许撤回'})
        if not 0<=int(time.time()*1000)-row['received_ms']<=RECALL_WINDOW_MS:
            fail(403,{'code':'YICHAT_REDACTION_WINDOW','error':'消息只能在发送后5分钟内撤回'})
        event_id=await recall(who,info,message_id,None if own else OWNER_RECALL_REASON)
        return {'conversation_id':room,'message_id':message_id,'event_id':event_id,'already_recalled':False}

    def txn_of(value):
        txn=value or secrets.token_hex(16)
        if not isinstance(txn,str) or not TXN.fullmatch(txn):fail(400,'txn_id 需为 1～64 个字母、数字或 . _ -')
        return txn

    @app.post('/open/v1/conversations/{room}/messages')
    async def send_text(request:Request,room:str):
        who=api_owner(request)
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':fail(415,'需要 Content-Type: application/json')
        try:data=await request.json()
        except ValueError:fail(400,'JSON 无效')
        if not isinstance(data,dict) or set(data)-{'text','txn_id'}:fail(400,'只接受 text 和 txn_id 两个字段')
        text=data.get('text')
        if not isinstance(text,str) or not text.strip() or len(text)>MAX_TEXT:fail(400,'text 需为 1～2000 个字符（Unicode 码点）')
        txn=txn_of(data.get('txn_id'));info=writable(who,room)
        event_id=await send_message(who,info,{'msgtype':'m.text','body':text},txn)
        return {'event_id':event_id,'conversation_id':room,'txn_id':txn}

    @app.post('/open/v1/conversations/{room}/files')
    async def send_file(request:Request,room:str,name:str='',type:str='auto',txn_id:str=''):
        who=api_owner(request)
        if not FILENAME.fullmatch(name.strip()):fail(400,'name 需为 1～200 个字符，不能包含控制字符、/ 或 \\')
        name=name.strip()
        if type not in ('auto','image','file'):fail(400,'type 只能是 auto、image 或 file')
        mime=request.headers.get('content-type','application/octet-stream').split(';')[0].strip().lower()
        if not MIME.fullmatch(mime):fail(400,'Content-Type 无效')
        if type=='image' and mime not in IMAGE_TYPES:fail(415,{'code':'YICHAT_IMAGE_TYPE','error':'图片只支持 PNG、JPEG、GIF、WebP、AVIF'})
        msgtype='m.image' if mime in IMAGE_TYPES and type!='file' else 'm.file'
        try:size=int(request.headers['content-length'])
        except (KeyError,ValueError):fail(411,'需要 Content-Length')
        if size<=0:fail(400,'文件不能为空')
        if size>s.max_file:fail(413,'文件超过 100 MB（100000000 字节）')
        txn=txn_of(txn_id);info=writable(who,room)
        who=await matrix_session(who)
        token=request.headers.get('authorization','').removeprefix('Bearer ')
        stored=await app.state.store_upload(who,info,room,request.stream(),size,lambda:owner(s,token))
        content={'msgtype':msgtype,'body':name,'info':{'mimetype':mime,'size':stored['bytes']},'url':stored['url'],archive.ATTACHMENT_KEY:[stored['id']]}
        event_id=await send_message(who,info,content,txn)
        return {'event_id':event_id,'conversation_id':room,'txn_id':txn,'kind':MEDIA[msgtype][0],
                'attachment':{'id':stored['id'],'name':name,'mimetype':mime,'size':stored['bytes'],'expires':stored['expires'],'download_url':'/open/v1/files/'+stored['id']}}

    @app.get('/open/v1/files/{fid}')
    async def download_file(request:Request,fid:str):
        who=api_owner(request)
        if not FILE_ID.fullmatch(fid):fail(404,'附件不存在')
        token=request.headers.get('authorization','').removeprefix('Bearer ')
        return await app.state.serve_file(lambda:owner(s,token),fid)

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
        rows,visible=await asyncio.to_thread(read);describe=describer()
        gone=redacted_ids(s,room,[r['event_id'] for r in visible])
        more=len(rows)>limit
        return {'conversation_id':room,'start':start,'end':end,
                'messages':[message(s,r,names,r['event_id'] in gone,describe) for r in visible],
                'next_after':str(rows[limit-1]['sequence']) if more else None}

    @app.websocket('/open/v1/stream')
    async def stream(ws:WebSocket):
        auth=ws.headers.get('authorization','')
        conversation=ws.query_params.get('conversation') or None
        try:
            if not auth.startswith('Bearer '):raise key_error('需要 Authorization: Bearer <API Key>')
            who=owner(s,auth.removeprefix('Bearer '))
            request_limit(s,'api-user:'+who['subject'],limit=RATE)
            if conversation is not None and (not ROOM.fullmatch(conversation) or conversation not in current_rooms(s,who['mxid'])):raise HTTPException(403,NOT_MEMBER)
        except HTTPException as e:
            # Accept first so the client can read the close code and reason.
            await ws.accept()
            code,close={429:('YICHAT_RATE_LIMITED',4429),403:('YICHAT_NOT_MEMBER',4403)}.get(e.status_code,('YICHAT_API_KEY_INVALID',4401))
            await ws.send_json({'type':'closing','code':code,'error':e.detail.get('error') if isinstance(e.detail,dict) else str(e.detail)})
            await ws.close(code=close);return
        if streams.get(who['subject'],0)>=MAX_STREAMS:
            await ws.accept()
            await ws.send_json({'type':'closing','code':'YICHAT_STREAM_LIMIT','error':'每个账户最多同时运行 3 个订阅'})
            await ws.close(code=4409);return
        streams[who['subject']]=streams.get(who['subject'],0)+1
        try:
            touch(s,who)
            await ws.accept()
            await run_stream(ws,who,conversation)
        except WebSocketDisconnect:pass
        except RuntimeError:
            # Socket already closed by the reader, or an archive integrity
            # failure. Fixed message only: no keys, bodies or IDs in logs.
            log.warning('Developer API stream ended early')
            with __import__('contextlib').suppress(Exception):await ws.close(code=1011)
        finally:
            streams[who['subject']]-=1
            if not streams[who['subject']]:del streams[who['subject']]

    async def run_stream(ws,who,conversation=None):
        def high():
            with archive.connect(s.archive_reader) as pg:return pg.execute('SELECT coalesce(max(sequence),0) n FROM events').fetchone()['n']
        cursor=await asyncio.to_thread(high)
        await ws.send_json({'type':'ready','server_time':int(time.time()*1000),'ping_interval':PING_SECONDS,'conversation_id':conversation})
        closed=asyncio.Event();names=Names(s);describe=describer()

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
        last_sent=time.monotonic();checked=time.monotonic();receipts=set()

        async def mark_read(room,event_id):
            # A stream subscribed to one conversation is the account reading it:
            # the peer sees the double tick as with the apps. Best effort only.
            nonlocal who
            try:
                who=await matrix_session(who)
                path='/_matrix/client/v3/rooms/'+quote(room,safe='')+'/receipt/m.read/'+quote(event_id,safe='')
                await app.state.matrix.call('POST',path,app.state.matrix.token(who),{})
            except HTTPException as e:
                detail=e.detail if isinstance(e.detail,dict) else {}
                if e.status_code==401 and detail.get('errcode')=='M_UNKNOWN_TOKEN':
                    with transaction(s.db) as c:c.execute('UPDATE api_keys SET device=NULL,upstream=NULL WHERE id=?',(who['key_id'],))
                    who={k:v for k,v in who.items() if k not in ('device','upstream')}
                log.warning('Developer API read receipt failed')
            except Exception:log.warning('Developer API read receipt failed')
        try:
            while not closed.is_set():
                if time.monotonic()-checked>=RECHECK_SECONDS:
                    checked=time.monotonic()
                    try:owner(s,ws.headers.get('authorization','').removeprefix('Bearer '))
                    except HTTPException:
                        await ws.send_json({'type':'closing','code':'YICHAT_API_KEY_INVALID','error':'API Key 已删除或账户不可用'})
                        await ws.close(code=4401);return
                rooms=current_rooms(s,who['mxid'])
                if conversation is not None:
                    if conversation not in rooms:
                        await ws.send_json({'type':'closing','code':'YICHAT_NOT_MEMBER','error':'你已不是此会话的成员'})
                        await ws.close(code=4403);return
                    rooms={conversation}
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
                        await ws.send_json({'type':'message','conversation_id':row['room'],'message':message(s,row,names,describe=describe)})
                    last_sent=time.monotonic()
                if conversation is not None:
                    latest=next((r for r,allowed in reversed(batch) if allowed and r['type'] in MESSAGE_TYPES and r['sender']!=who['mxid']),None)
                    if latest:
                        job=asyncio.create_task(mark_read(latest['room'],latest['event_id']));receipts.add(job);job.add_done_callback(receipts.discard)
                if time.monotonic()-last_sent>=PING_SECONDS:
                    await ws.send_json({'type':'ping','server_time':int(time.time()*1000)});last_sent=time.monotonic()
                if not batch or len(batch)<100:
                    try:await asyncio.wait_for(closed.wait(),POLL_SECONDS)
                    except asyncio.TimeoutError:pass
        finally:
            task.cancel()
