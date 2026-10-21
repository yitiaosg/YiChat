"""No private recipient room is visible before an accepted encrypted message."""
import asyncio,secrets,time
from urllib.parse import quote
from fastapi import Request
from . import policy,groups
from .db import connect,transaction
from .security import fail

def visible(s,info,mxid):
    if info['kind']!='direct' or mxid==info['owner']:return True
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
