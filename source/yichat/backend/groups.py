"""Server-authoritative group roles. Matrix remains the membership/event engine.
Management uses short-lived, server-only delegated tokens (no crypto devices).
A narrowly scoped persisted admission authorizes each delegated native event.
"""
import hashlib,json,secrets,time,re,asyncio
from urllib.parse import quote
from fastapi import Request,HTTPException
from .db import connect,transaction
from .security import fail,valid_sid
from . import membership,lifecycle

MARK='org.yitiao.group_operation'
SCHEMA='''
CREATE TABLE IF NOT EXISTS group_settings(room TEXT PRIMARY KEY REFERENCES rooms(room),number TEXT UNIQUE NOT NULL CHECK(length(number)=9),searchable INTEGER NOT NULL DEFAULT 0,member_invites INTEGER NOT NULL DEFAULT 1,seeded INTEGER NOT NULL DEFAULT 1,creator TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS group_members(room TEXT NOT NULL,mxid TEXT NOT NULL,admin INTEGER NOT NULL DEFAULT 0,muted_until INTEGER NOT NULL DEFAULT 0,banned INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(room,mxid));
CREATE TABLE IF NOT EXISTS group_operations(id TEXT PRIMARY KEY,room TEXT NOT NULL,actor TEXT NOT NULL,sid TEXT NOT NULL,username TEXT NOT NULL,request_key TEXT NOT NULL,payload TEXT NOT NULL,action TEXT NOT NULL,target TEXT,created REAL NOT NULL,done INTEGER NOT NULL DEFAULT 0,UNIQUE(actor,request_key));
CREATE TABLE IF NOT EXISTS group_admissions(id TEXT PRIMARY KEY REFERENCES group_operations(id),sender TEXT NOT NULL,type TEXT NOT NULL,state_key TEXT NOT NULL,digest TEXT NOT NULL,expires REAL NOT NULL,accepted TEXT);
'''

def register(c,room,seeded=1):
    if c.execute('SELECT 1 FROM group_settings WHERE room=?',(room,)).fetchone():return
    for _ in range(100):
        number=str(100000000+secrets.randbelow(900000000))
        if not c.execute('SELECT 1 FROM group_settings WHERE number=?',(number,)).fetchone():
            c.execute('INSERT INTO group_settings(room,number,seeded,creator) VALUES(?,?,?,(SELECT owner FROM rooms WHERE room=?))',(room,number,seeded,room));return
    raise RuntimeError('Group number allocation unavailable')

def role(c,room,mxid):
    g=c.execute('SELECT owner FROM rooms WHERE room=? AND kind=\'group\'',(room,)).fetchone()
    if not g:fail(403,'非群聊')
    member=c.execute('SELECT * FROM group_members WHERE room=? AND mxid=?',(room,mxid)).fetchone()
    return 'owner' if g['owner']==mxid else 'admin' if member and member['admin'] else 'member'

def joined(s,room):
    with connect(s.ledger) as c:return [r['mxid'] for r in c.execute("SELECT mxid FROM yichat_memberships WHERE room=? AND membership='join' AND pending_event IS NULL",(room,))]

def require_send(s,room,mxid):
    with connect(s.ledger) as ledger:
        lifecycle.require_active(ledger,room);membership.require(ledger,room,mxid)
    with connect(s.db) as c:r=c.execute('SELECT * FROM group_members WHERE room=? AND mxid=?',(room,mxid)).fetchone()
    if r and (r['banned'] or r['muted_until']>int(time.time()*1000)):
        fail(403,{'code':'YICHAT_GROUP_MUTED' if not r['banned'] else 'YICHAT_GROUP_BANNED','error':'你已被禁言，暂时不能发送消息或附件' if not r['banned'] else '你已被移出群组且不允许重新加入'})

def normalize(content):
    return hashlib.sha256(json.dumps({k:v for k,v in content.items() if k not in ('redacts','displayname','avatar_url')},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def admitted(s,room,sender,typ,content,state_key=''):
    opid=content.get(MARK)
    if not isinstance(opid,str):return None
    with connect(s.db) as c:r=c.execute('SELECT o.*,a.sender,a.type,a.state_key,a.digest,a.expires FROM group_operations o JOIN group_admissions a USING(id) WHERE o.id=? AND o.room=?',(opid,room)).fetchone()
    if not r or r['done'] or r['expires']<time.time() or r['sender']!=sender or r['type']!=typ or r['state_key']!=(state_key or '') or r['digest']!=normalize(content) or not (valid_sid(s,r['sid'],r['username']) or (r['action']=='account_transfer' and lifecycle.purged(s,r['actor']))):fail(403,'群管理授权无效或已过期')
    if r['action'] not in ('join','direct_join','account_transfer'):
        with connect(s.db) as c:permission(c,room,r['actor'],r['action'],r['target'])
    elif r['action']=='account_transfer':
        with connect(s.db) as c:
            if role(c,room,r['actor'])!='owner':fail(403,'账户群主已变更')
    elif r['action']=='join':
        with connect(s.db) as c:g=c.execute('SELECT searchable FROM group_settings WHERE room=?',(room,)).fetchone()
        if not g or not g['searchable']:fail(403,'群主已关闭群号加入')
    else:
        from . import policy
        policy.require_send(s,policy.room(s,room),r['actor'])
    return dict(r)

def apply(s,content,event_id):
    opid=content.get(MARK)
    if not opid:return
    with transaction(s.db) as c:
        op=c.execute('SELECT * FROM group_operations WHERE id=?',(opid,)).fetchone()
        if not op or op['done']:return
        if op['action'] in ('transfer','account_transfer'):
            c.execute('UPDATE rooms SET owner=? WHERE room=? AND owner=?',(op['target'],op['room'],op['actor']))
            c.execute('UPDATE group_members SET admin=0,muted_until=0 WHERE room=? AND mxid=?',(op['room'],op['target']))
        if op['action']=='avatar':
            from .group_avatars import publish
            publish(c,op['room'],json.loads(op['payload']).get('image'))
        if op['action']=='kick':
            data=json.loads(op['payload']);c.execute('INSERT INTO group_members(room,mxid,admin,banned) VALUES(?,?,0,?) ON CONFLICT(room,mxid) DO UPDATE SET admin=0,banned=excluded.banned',(op['room'],op['target'],int(bool(data.get('ban')))))
        c.execute('UPDATE group_operations SET done=1 WHERE id=?',(opid,))
        c.execute('UPDATE group_admissions SET accepted=? WHERE id=?',(event_id,opid))

def validate_member(s,info,sender,target,content,control=None):
    m=content.get('membership');room=info['room']
    if m in ('invite','join') and lifecycle.purged(s,target):fail(403,'该账户已注销，不能加入群聊')
    with connect(s.ledger) as ledger:
        if m!='leave':lifecycle.require_active(ledger,room)
    with connect(s.db) as c:
        settings=c.execute('SELECT * FROM group_settings WHERE room=?',(room,)).fetchone()
        ban=c.execute('SELECT banned FROM group_members WHERE room=? AND mxid=?',(room,target)).fetchone()
        if m in ('invite','join') and ban and ban['banned']:fail(403,'该用户被禁止重新加入群组')
        if control:return
        if m=='invite' and settings and not settings['member_invites'] and role(c,room,sender)=='member':fail(403,'群主已关闭成员邀请')
        if m in ('ban',) or (m=='leave' and sender!=target):fail(403,'请通过群管理接口移除成员')
        if m=='leave' and sender==target==info['owner'] and len(joined(s,room))>1 and not lifecycle.status(s,room) and not lifecycle.purged(s,sender):fail(409,'请先转让群主，再退出群聊')

def permission(c,room,actor,action,target=None):
    rank=role(c,room,actor)
    if action in ('settings','transfer','admin','unban','avatar','dissolve') and rank!='owner':fail(403,'只有群主可以执行此操作')
    if action in ('name','topic') and rank not in ('owner','admin'):fail(403,'需要群主或管理员权限')
    if action in ('mute','kick','redact'):
        if rank not in ('owner','admin'):fail(403,'需要群主或管理员权限')
        target_role=role(c,room,target)
        if actor==target or target_role=='owner' or (rank=='admin' and target_role!='member') or (action=='redact' and rank!='owner'):fail(403,'不能管理此成员')
    return rank

async def delegate(matrix,s,mxid,method,path,body):
    auth=await matrix.call('POST','/_synapse/admin/v1/users/'+quote(mxid,safe='')+'/login',data={'valid_until_ms':int(time.time()*1000)+120000})
    token=auth['access_token']
    try:return await matrix.call(method,path,token,body)
    finally:
        try:await matrix.call('POST','/_matrix/client/v3/logout',token,{})
        except Exception:pass # Token is independently bounded to two minutes.

def install(app,s,session,room_access):
    locks={}
    async def read(who,room):
        _,info=await room_access_dummy(who,room)
        with connect(s.db) as c:settings=dict(c.execute('SELECT * FROM group_settings WHERE room=?',(room,)).fetchone())
        if not settings['seeded']:
            levels=await app.state.matrix.call('GET','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/state/m.room.power_levels',app.state.matrix.token(who))
            with transaction(s.db) as c:
                for uid,level in levels.get('users',{}).items():
                    if uid!=info['owner'] and level>=50 and c.execute('SELECT count(*) FROM group_members WHERE room=? AND admin=1',(room,)).fetchone()[0]<8:c.execute('INSERT INTO group_members(room,mxid,admin) VALUES(?,?,1) ON CONFLICT(room,mxid) DO UPDATE SET admin=1',(room,uid))
                c.execute('UPDATE group_settings SET seeded=1 WHERE room=?',(room,))
        ids=joined(s,room)
        with connect(s.db) as c:
            settings=dict(c.execute('SELECT * FROM group_settings WHERE room=?',(room,)).fetchone());myrole=role(c,room,who['mxid'])
            extra={r['mxid']:dict(r) for r in c.execute('SELECT * FROM group_members WHERE room=?',(room,))}
            members=[dict(mxid=mxid,role='owner' if mxid==info['owner'] else 'admin' if extra.get(mxid,{}).get('admin') else 'member',muted_until=extra.get(mxid,{}).get('muted_until',0),banned=bool(extra.get(mxid,{}).get('banned',0)),joined=mxid in ids) for mxid in sorted(set(ids)|{k for k,v in extra.items() if v['banned']})]
        former=who['mxid'] not in ids
        if former:members=[];myrole='former'
        return dict(revision=info.get('revision',0),dissolved_at=lifecycle.status(s,room),admin_count=sum(m['role']=='admin' and m['joined'] for m in members),admin_limit=8,room=room,owner=info['owner'],number=settings['number'],searchable=bool(settings['searchable']),member_invites=bool(settings['member_invites']),role=myrole,members=members,member_count=None if former else len(ids),can_invite=not former and not lifecycle.status(s,room) and (myrole!='member' or bool(settings['member_invites'])))
    async def room_access_dummy(who,room):
        from . import policy
        info=policy.room(s,room)
        if info['kind']!='group':fail(403,'非群聊')
        with connect(s.ledger) as c:
            member=c.execute('SELECT membership,event_id FROM yichat_memberships WHERE room=? AND mxid=?',(room,who['mxid'])).fetchone()
            if not member or not member['event_id']:fail(403,'没有此群聊记录的访问权限')
        return who,info
    @app.get('/api/groups/{room}')
    async def detail(request:Request,room:str):return await read(session(request),room)
    @app.get('/api/group-numbers/{number}')
    async def lookup(request:Request,number:str):
        session(request)
        with connect(s.db) as c:r=c.execute('SELECT g.*,r.owner FROM group_settings g JOIN rooms r USING(room) WHERE number=? AND searchable=1',(number,)).fetchone()
        if not r or not re.fullmatch(r'[1-9][0-9]{8}',number) or r['owner'] not in joined(s,r['room']) or lifecycle.status(s,r['room']):fail(404,'群号不存在或未开放搜索')
        return {'room':r['room'],'number':number,'member_count':len(joined(s,r['room']))}
    async def emit(who,info,op,actor,typ,content,state_key='',endpoint=None):
        content=dict(content,**{MARK:op['id']})
        with transaction(s.db) as c:c.execute('INSERT INTO group_admissions VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET expires=excluded.expires',(op['id'],actor,typ,state_key,normalize(content),time.time()+120))
        path='/_matrix/client/v3/rooms/'+quote(info['room'],safe='')+'/'
        suffix=endpoint or ('state/'+typ+'/'+quote(state_key,safe=''))
        result=await delegate(app.state.matrix,s,actor,'PUT' if endpoint is None or endpoint.startswith('redact/') else 'POST',path+suffix,content)
        # Verify/persist the management effect even if callback acknowledgement lags.
        apply(s,content,result.get('event_id','accepted'))
        return result
    @app.post('/api/groups/{room}/manage')
    async def manage(request:Request,room:str):
        who=session(request);data=await request.json();action=data.get('action');target=data.get('target');key=data.get('key')
        if action not in ('settings','transfer','admin','mute','kick','unban','redact','name','topic','avatar','dissolve') or not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{16,100}',key):fail(400,'无效群管理请求')
        if action in ('transfer','admin','mute','kick','unban','redact') and not isinstance(target,str):fail(400,'需要目标成员')
        async with locks.setdefault(room,asyncio.Lock()):
            _,info=await room_access_dummy(who,room)
            await read(who,room)
            with connect(s.ledger) as ledger:
                membership.require(ledger,room,who['mxid'])
                if action!='dissolve':lifecycle.require_active(ledger,room)
            payload=json.dumps(data,sort_keys=True,ensure_ascii=False)
            ids=joined(s,room)
            with transaction(s.db) as c:
                old=c.execute('SELECT * FROM group_operations WHERE actor=? AND request_key=?',(who['mxid'],key)).fetchone()
                if old and (old['payload']!=payload or old['room']!=room):fail(409,'请求键已用于不同操作')
                if not old:
                    rank=permission(c,room,who['mxid'],action,target)
                    if action not in ('settings','unban','redact','name','topic','avatar','dissolve') and target not in ids:fail(409,'目标不是当前群成员')
                    if action=='kick' and data.get('ban') and rank!='owner':fail(403,'只有群主可以禁止再次加入')
                    if action=='mute' and (type(data.get('seconds'))!=int or not 0<=data['seconds']<=86400):fail(400,'单次禁言最多24小时')
                    if action in ('settings','admin') and any(type(data.get(k))!=bool for k in (('searchable','member_invites') if action=='settings' else ('enabled',))):fail(400,'需要布尔设置')
                    if action=='admin' and role(c,room,target)=='owner':fail(400,'群主不需要设置为管理员')
                    if action=='admin' and data['enabled'] and not c.execute('SELECT 1 FROM group_members WHERE room=? AND mxid=? AND admin=1',(room,target)).fetchone() and c.execute('SELECT count(*) FROM group_members WHERE room=? AND admin=1',(room,)).fetchone()[0]>=8:fail(409,'群聊最多设置8个管理员')
                    if action=='transfer' and target==who['mxid']:fail(400,'新群主不能是本人')
                    oid=secrets.token_urlsafe(24)
                    c.execute('INSERT INTO group_operations VALUES(?,?,?,?,?,?,?,?,?,?,0)',(oid,room,who['mxid'],who['sid'],who['username'],key,payload,action,target,time.time()))
                    old=c.execute('SELECT * FROM group_operations WHERE id=?',(oid,)).fetchone()
                    if action in ('admin','mute','unban','kick'):
                        c.execute('INSERT OR IGNORE INTO group_members(room,mxid) VALUES(?,?)',(room,target))
                        if action=='admin':c.execute('UPDATE group_members SET admin=? WHERE room=? AND mxid=?',(int(data['enabled']),room,target))
                        if action=='mute':c.execute('UPDATE group_members SET muted_until=? WHERE room=? AND mxid=?',(int(time.time()*1000)+1000*data['seconds'] if data['seconds'] else 0,room,target))
                        if action=='unban':c.execute('UPDATE group_members SET banned=?,admin=0 WHERE room=? AND mxid=?',(int(bool(data.get('ban'))) if action=='kick' else 0,room,target))
                    if action=='settings':c.execute('UPDATE group_settings SET searchable=?,member_invites=? WHERE room=?',(int(data['searchable']),int(data['member_invites']),room))
                    if action in ('settings','admin','mute','unban'):c.execute('UPDATE group_operations SET done=1 WHERE id=?',(oid,))
                op=dict(old)
            if not op['done']:
                with connect(s.db) as c:permission(c,room,who['mxid'],action,target)
            if action=='dissolve':
                if not op['done']:
                    with transaction(s.ledger) as ledger:
                        with connect(s.db) as db:permission(db,room,who['mxid'],action)
                        ledger.execute('INSERT INTO yichat_group_lifecycle(room,dissolved_ms) VALUES(?,?) ON CONFLICT(room) DO UPDATE SET dissolved_ms=coalesce(dissolved_ms,excluded.dissolved_ms)',(room,int(time.time()*1000)))
                    with transaction(s.db) as db:db.execute('UPDATE group_operations SET done=1 WHERE id=?',(op['id'],))
                return await read(who,room)
            if not op['done'] and action in ('transfer','kick','redact','name','topic','avatar'):
                if action=='avatar':
                    from .group_avatars import content
                    await emit(who,info,op,info['owner'],'m.room.avatar',content(s,who,room,data.get('image')))
                elif action in ('name','topic'):
                    value=data.get('value')
                    if not isinstance(value,str) or len(value)>(100 if action=='name' else 2000):fail(400,'群资料过长')
                    await emit(who,info,op,info['owner'],'m.room.'+action,{action:value})
                elif action=='transfer':
                    levels=await app.state.matrix.call('GET','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/state/m.room.power_levels',app.state.matrix.token(who))
                    create=await app.state.matrix.call('GET','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/state/m.room.create',app.state.matrix.token(who))
                    # v12 creator power is immutable. Preserve old powers, enforce
                    # all management privileges through this authoritative registry.

                    with connect(s.db) as c:creator=c.execute('SELECT creator FROM group_settings WHERE room=?',(room,)).fetchone()[0]
                    v12=create.get('room_version','1')=='12'
                    if not v12 or target!=creator:levels.setdefault('users',{})[target]=100
                    await emit(who,info,op,info['owner'],'m.room.power_levels',levels)
                elif action=='kick':await emit(who,info,op,target,'m.room.member',{'membership':'leave','reason':'由 '+who['username']+' 移出群聊'},target)
                else:
                    event_id=data.get('event');raw=await app.state.matrix.call('GET','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/event/'+quote(str(event_id),safe=''),app.state.matrix.token(who))
                    if raw.get('sender')!=target or raw.get('type') not in ('m.room.message','m.room.encrypted'):fail(403,'无效撤回目标')
                    await emit(who,info,op,info['owner'],'m.room.redaction',{'reason':'群主撤回成员消息'},endpoint='redact/'+quote(event_id,safe='')+'/'+op['id'])
            result=await read(who,room)
            if action=='transfer' and result['owner']!=target:fail(409,'群主已被其他操作变更，请刷新群资料')
            return result
    @app.post('/api/group-numbers/{number}/join')
    async def join_number(request:Request,number:str):
        who=session(request);g=await lookup(request,number);room=g['room']
        if who['mxid'] in joined(s,room):return {'room_id':room}
        from . import policy
        info=policy.room(s,room)
        # Owner delegates invitation; module rechecks the live search flag and ban.
        with connect(s.db) as c:r=c.execute('SELECT banned FROM group_members WHERE room=? AND mxid=?',(room,who['mxid'])).fetchone()
        if r and r['banned']:fail(403,'你被禁止重新加入此群')
        data=await request.json();key=data.get('key')
        if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{16,100}',key):fail(400,'需要稳定请求键')
        opid=secrets.token_urlsafe(24)
        with transaction(s.db) as c:
            old=c.execute('SELECT * FROM group_operations WHERE actor=? AND request_key=?',(who['mxid'],key)).fetchone()
            if old and (old['action']!='join' or old['room']!=room):fail(409,'请求键已使用')
            if not old:c.execute('INSERT INTO group_operations VALUES(?,?,?,?,?,?,?,?,?,?,0)',(opid,room,who['mxid'],who['sid'],who['username'],key,'{}','join',who['mxid'],time.time()))
            op=dict(c.execute('SELECT * FROM group_operations WHERE actor=? AND request_key=?',(who['mxid'],key)).fetchone())
        if not op['done']:await emit(who,info,op,info['owner'],'m.room.member',{'membership':'invite'},who['mxid'])
        await app.state.matrix.call('POST','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/join',app.state.matrix.token(who),{})
        return {'room_id':room}
