"""Durable read-only group closure; no room/history/key deletion."""
import time
from .db import connect,transaction
from .security import fail
SCHEMA='''CREATE TABLE IF NOT EXISTS yichat_group_lifecycle(room TEXT PRIMARY KEY,had_multiple INTEGER NOT NULL DEFAULT 0,dissolved_ms INTEGER);'''

def status(s,room):
    with connect(s.ledger) as c:r=c.execute('SELECT dissolved_ms FROM yichat_group_lifecycle WHERE room=?',(room,)).fetchone()
    return r['dissolved_ms'] if r else None

def require_active(c,room):
    r=c.execute('SELECT dissolved_ms FROM yichat_group_lifecycle WHERE room=?',(room,)).fetchone()
    if r and r['dissolved_ms']:fail(403,{'code':'YICHAT_GROUP_DISSOLVED','error':'群聊已解散，仅可查看以前的记录'})

def transition(c,room):
    count=c.execute("SELECT count(*) FROM yichat_memberships WHERE room=? AND membership='join' AND pending_event IS NULL",(room,)).fetchone()[0]
    c.execute('INSERT OR IGNORE INTO yichat_group_lifecycle(room) VALUES(?)',(room,))
    if count>=2:c.execute('UPDATE yichat_group_lifecycle SET had_multiple=1 WHERE room=?',(room,))
    elif count<=1 and not c.execute('SELECT 1 FROM yichat_memberships WHERE room=? AND pending_event IS NOT NULL LIMIT 1',(room,)).fetchone():c.execute('UPDATE yichat_group_lifecycle SET dissolved_ms=coalesce(dissolved_ms,?) WHERE room=? AND had_multiple=1',(int(time.time()*1000),room))

def purged(s,mxid):
    with connect(s.db) as c:r=c.execute('SELECT username FROM identities WHERE mxid=?',(mxid,)).fetchone()
    if not r:return False
    with connect(s.accounts) as c:user=c.execute('SELECT * FROM users WHERE username=?',(r['username'],)).fetchone()
    return not user or bool(dict(user).get('purged_at'))

async def retire_accounts(module):
    """Run bounded, restartable account retirement with official Synapse APIs."""
    import json,secrets
    from . import groups,policy
    s=module.s
    with connect(s.db) as db:people=db.execute('SELECT username,mxid FROM identities').fetchall()
    with connect(s.accounts) as c:
        if not any(r[1]=='purged_at' for r in c.execute('PRAGMA table_info(users)')):return
        gone={r['username'] for r in c.execute('SELECT username FROM users WHERE purged_at IS NOT NULL')}
    if not gone:return
    with connect(s.db) as db:known={row[0] for row in db.execute('SELECT room FROM rooms')}
    for person in people:
        if person['username'] not in gone:continue
        mxid=person['mxid']
        with transaction(s.ledger) as ledger:
            ledger.execute('UPDATE yidt_sessions SET revoked=1 WHERE user=? AND revoked=0',(person['username'],))
            rooms=ledger.execute("SELECT room FROM yichat_memberships WHERE mxid=? AND membership IN ('join','invite') AND pending_event IS NULL",(mxid,)).fetchall()
        queue=sorted([r for r in rooms if r['room'] in known],key=lambda r:r['room'])
        cursor=getattr(module,'retirement_cursor',{}).get(mxid,'')
        queue=[r for r in queue if r['room']>cursor]+[r for r in queue if r['room']<=cursor]
        for row in queue[:10]:
            if not hasattr(module,'retirement_cursor'):module.retirement_cursor={}
            module.retirement_cursor[mxid]=row['room']
            try:
                room=row['room'];info=policy.room(s,room)
                if info['kind']=='group' and info['owner']==mxid and not status(s,room):
                    ids=[uid for uid in groups.joined(s,room) if uid!=mxid and not purged(s,uid)]
                    if ids:
                        with connect(s.db) as db:admins={r[0] for r in db.execute('SELECT mxid FROM group_members WHERE room=? AND admin=1',(room,))}
                        target=sorted(ids,key=lambda uid:(uid not in admins,uid))[0]
                        state=await module.api.get_room_state(room,[('m.room.power_levels',''),('m.room.create','')])
                        raw=state.get(('m.room.power_levels',''));levels={}
                        # Unfreeze nested immutable state using the same adapter as the module.
                        from .synapse_module import unfreeze
                        levels=unfreeze(raw.content) if raw else {}
                        create=state.get(('m.room.create',''));v12=create and create.content.get('room_version')=='12'
                        with connect(s.db) as db:creator=db.execute('SELECT creator FROM group_settings WHERE room=?',(room,)).fetchone()[0]
                        if not v12 or target!=creator:levels.setdefault('users',{})[target]=100
                        oid=secrets.token_urlsafe(24);levels[groups.MARK]=oid
                        with transaction(s.db) as db:
                            db.execute('INSERT INTO group_operations VALUES(?,?,?,?,?,?,?,?,?,?,0)',(oid,room,mxid,'',person['username'],oid,'{}','account_transfer',target,time.time()))
                            db.execute('INSERT INTO group_admissions VALUES(?,?,?,?,?,?,NULL)',(oid,mxid,'m.room.power_levels','',groups.normalize(levels),time.time()+120))
                        event=await module.api.create_and_send_event_into_room({'type':'m.room.power_levels','room_id':room,'sender':mxid,'state_key':'','content':levels})
                        groups.apply(s,levels,event.event_id)
                await module.api.update_room_membership(mxid,mxid,room,'leave',{'reason':'账户已注销','displayname':person['username'],'avatar_url':''})

            except Exception:
                import logging
                logging.getLogger('yichat.module').exception('Account room retirement pending; other rooms continue')
