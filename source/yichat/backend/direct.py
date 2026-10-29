"""No private recipient room is visible before an accepted message."""
import asyncio,secrets,time
from urllib.parse import quote
from fastapi import Request
from . import policy,groups
from .db import connect,transaction
from .security import fail

SCHEMA="""CREATE TABLE IF NOT EXISTS direct_pairs(first TEXT NOT NULL,second TEXT NOT NULL,room TEXT UNIQUE,ticket TEXT UNIQUE,PRIMARY KEY(first,second));"""

def pair(a,b):return tuple(sorted((a,b)))

def reopen(s,who,room):
    with transaction(s.db) as c:
        c.execute('INSERT INTO conversation_entries(subject,room,opened) VALUES(?,?,1) ON CONFLICT(subject,room) DO UPDATE SET opened=1,deleted=0',(who['subject'],room))
        c.execute('UPDATE conversation_preferences SET archived=0 WHERE subject=? AND room=?',(who['subject'],room))
    return {'room_id':room}

async def open_existing(app,s,who,room):
    info=policy.room(s,room)
    if who['mxid'] not in (info['owner'],info['peer']):fail(403,'不是私聊参与者')
    if lifecycle_purged(s,who['mxid']):fail(403,'账户已注销')
    locks=getattr(app.state,'direct_open_locks',None)
    if locks is None:locks=app.state.direct_open_locks={}
    reopen(s,who,room)
    async with locks.setdefault(room,asyncio.Lock()):
        if who['mxid'] not in groups.joined(s,room):
            inviter=info['peer'] if who['mxid']==info['owner'] else info['owner']
            for attempt in range(5):
                members=groups.joined(s,room)
                if who['mxid'] in members:return {'room_id':room}
                if inviter in members:break
                if attempt<4:await asyncio.sleep(.25)
            else:fail(409,{'code':'YICHAT_ROOM_CREATE_PENDING','error':'原私聊成员状态正在初始化，请稍后核实，不会新建重复会话'})
            for membership,sender in [('invite',inviter),('join',who['mxid'])]:
                oid=secrets.token_urlsafe(24);content={'membership':membership,groups.MARK:oid}
                with transaction(s.db) as c:
                    c.execute('INSERT INTO group_operations VALUES(?,?,?,?,?,?,?,?,?,?,0)',(oid,room,who['mxid'],who['sid'],who['username'],oid,'{}','direct_open',who['mxid'],time.time()))
                    c.execute('INSERT INTO group_admissions VALUES(?,?,?,?,?,?,NULL)',(oid,sender,'m.room.member',who['mxid'],groups.normalize(content),time.time()+120))
                await groups.delegate(app.state.matrix,s,sender,'PUT','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/state/m.room.member/'+quote(who['mxid'],safe=''),content)
    return {'room_id':room}

def lifecycle_purged(s,mxid):
    from .lifecycle import purged
    return purged(s,mxid)

def reserve_creation(s,who,kind,invite,key,payload):
    first,second=pair(who['mxid'],invite[0]) if kind=='direct' else ('','')
    with transaction(s.db) as db:
        if key:
            old=db.execute('SELECT r.payload,t.room,t.expires FROM room_requests r JOIN room_tickets t ON t.token=r.ticket WHERE r.subject=? AND r.request_key=?',(who['subject'],key)).fetchone()
            if old:
                if old['payload']!=payload:fail(409,'创建请求键已用于不同会话')
                if old['room']:return None,old['room']
                fail(409,{'code':'YICHAT_ROOM_CREATE_PENDING','error':'原创建结果待确认，请查询原请求，勿重新创建'})
        if kind=='direct':
            current=db.execute('SELECT p.room,p.ticket,t.expires FROM direct_pairs p LEFT JOIN room_tickets t ON t.token=p.ticket WHERE first=? AND second=?',(first,second)).fetchone()
            if current and current['room']:
                return None,current['room']
            if current and current['ticket'] and current['expires']>time.time():fail(409,{'code':'YICHAT_ROOM_CREATE_PENDING','error':'双方的私聊正在创建，请稍后核实，不会重复创建'})
        ticket=secrets.token_urlsafe(32)
        db.execute('INSERT INTO room_tickets VALUES(?,?,?,?,?,NULL)',(ticket,who['mxid'],kind,invite[0] if kind=='direct' else None,time.time()+120))
        if key:db.execute('INSERT INTO room_requests VALUES(?,?,?,?,?)',(who['subject'],key,payload,ticket,time.time()))
        if kind=='direct':db.execute('INSERT INTO direct_pairs VALUES(?,?,NULL,?) ON CONFLICT(first,second) DO UPDATE SET ticket=excluded.ticket',(first,second,ticket))
    return ticket,None

def visible(s,info,mxid):
    if info['kind']!='direct' or mxid==info['owner']:return True
    with connect(s.db) as c:
        if c.execute('SELECT 1 FROM conversation_entries e JOIN identities i ON i.subject=e.subject WHERE e.room=? AND i.mxid=? AND e.opened=1',(info['room'],mxid)).fetchone():return True
    with connect(s.db) as c:r=c.execute('SELECT visible FROM direct_visibility WHERE room=?',(info['room'],)).fetchone()
    return bool(r and r['visible'])

def install(app,s,session):
    locks={};seen=set()
    @app.post('/api/rooms/{room}/prepare')
    async def prepare(request:Request,room:str):
        who=session(request);info=policy.room(s,room)
        if info['kind']!='direct':return {'ready':True}
        if who['mxid'] not in (info['owner'],info['peer']):fail(403,'不是私聊参与者')
        policy.require_send(s,info,who['mxid'])
        async with locks.setdefault(room,asyncio.Lock()):
            policy.require_send(s,info,who['mxid'])
            await app.state.matrix.membership(who,room)
            peer=info['peer'] if who['mxid']==info['owner'] else info['owner']
            if peer not in groups.joined(s,room):
                await app.state.matrix.call('POST','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/invite',app.state.matrix.token(who),{'user_id':peer})
                oid=secrets.token_urlsafe(24);content={'membership':'join',groups.MARK:oid}
                with transaction(s.db) as c:
                    c.execute('INSERT INTO group_operations VALUES(?,?,?,?,?,?,?,?,?,?,0)',(oid,room,who['mxid'],who['sid'],who['username'],oid,'{}','direct_join',peer,time.time()))
                    c.execute('INSERT INTO group_admissions VALUES(?,?,?,?,?,?,NULL)',(oid,peer,'m.room.member',peer,groups.normalize(content),time.time()+120))
                await groups.delegate(app.state.matrix,s,peer,'PUT','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/state/m.room.member/'+quote(peer,safe=''),content)
            return {'ready':True}
    async def filter_sync(who,data):
        for category in ('join','invite','leave'):
            rows=data.get('rooms',{}).get(category,{})
            for room in list(rows):
                if policy.retired(s,room):del rows[room];continue
                try:info=policy.room(s,room)
                except Exception:continue
                if not visible(s,info,who['mxid']):del rows[room];continue
                if category!='join' or info['kind']!='direct' or who['mxid']==info['owner']:continue
                events=rows[room].setdefault('state',{}).setdefault('events',[])
                key=(room,who['mxid'],who['device'])
                if any(e['type']=='m.room.encryption' for e in events):
                    seen.add(key);continue
                if key in seen:continue
                state=await app.state.matrix.call('GET','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/state',app.state.matrix.token(who))
                structural=[e for e in state if e['type'] in ('m.room.create','m.room.encryption','m.room.history_visibility') or (e['type']=='m.room.member' and e.get('state_key') in (info['owner'],info['peer']))]
                have={(e['type'],e.get('state_key','')) for e in events}
                events[:0]=[e for e in structural if (e['type'],e.get('state_key','')) not in have]
                seen.add(key)
                if len(seen)>8192:seen.clear();seen.add(key)
        return data
    app.state.filter_direct_sync=filter_sync
