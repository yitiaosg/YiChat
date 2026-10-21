"""Per-account preferences and aggregate, ciphertext-free notification hints."""
import time,re
from fastapi import Request
from .db import connect,transaction
from .security import fail
from . import policy,lifecycle
SCHEMA='''CREATE TABLE IF NOT EXISTS conversation_preferences(subject TEXT NOT NULL,room TEXT NOT NULL,pinned INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,muted INTEGER NOT NULL DEFAULT 0,read_sequence INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(subject,room));'''

def accessible(s,who,room):
    info=policy.room(s,room)
    with connect(s.ledger) as c:r=c.execute('SELECT event_id FROM yichat_memberships WHERE room=? AND mxid=?',(room,who['mxid'])).fetchone()
    if not r or not r['event_id']:fail(403,'没有此会话的访问权限')
    return info

def install(app,s,session):
    @app.get('/api/conversations/preferences')
    async def preferences(request:Request):
        who=session(request)
        with connect(s.db) as c:return [dict(r) for r in c.execute('SELECT room,pinned,archived,muted FROM conversation_preferences WHERE subject=?',(who['subject'],))]
    @app.post('/api/conversations/{room}/preferences')
    async def preference(request:Request,room:str):
        who=session(request);accessible(s,who,room);data=await request.json()
        if not isinstance(data,dict) or not data or not set(data)<= {'pinned','archived','muted','read','event'} or any(type(v)!=bool for k,v in data.items() if k!='event') or ('event' in data and (not isinstance(data['event'],str) or len(data['event'])>255 or not data.get('read'))):fail(400,'需要有效的会话设置')
        with transaction(s.db) as c:
            c.execute('INSERT OR IGNORE INTO conversation_preferences(subject,room) VALUES(?,?)',(who['subject'],room))
            for key,value in data.items():
                if key=='event':continue
                if key=='read':
                    if value:
                        receipt=c.execute('SELECT sequence FROM history_receipts WHERE room=? AND event_id=?',(room,data.get('event',''))).fetchone()
                        if not receipt:fail(503,'已读事件的接受存证正在同步，请稍后重试')
                        c.execute('UPDATE conversation_preferences SET read_sequence=max(read_sequence,?) WHERE subject=? AND room=?',(receipt['sequence'],who['subject'],room))
                else:c.execute('UPDATE conversation_preferences SET '+key+'=? WHERE subject=? AND room=?',(int(value),who['subject'],room))
            return dict(c.execute('SELECT room,pinned,archived,muted FROM conversation_preferences WHERE subject=? AND room=?',(who['subject'],room)).fetchone())
    @app.get('/api/notifications')
    async def notifications(request:Request,after:int|None=None):
        who=session(request)
        with connect(s.db) as c:
            high=c.execute('SELECT coalesce(max(sequence),0) FROM history_receipts').fetchone()[0]
            if after is None:return {'cursor':str(high),'rooms':[]}
            if not 0<=after<=9223372036854775807:fail(400,'无效通知游标')
            if after>high:fail(503,'通知存证游标尚未追平，请稍后重试')
            records=c.execute("SELECT h.room,h.event_id event,h.sequence FROM history_receipts h LEFT JOIN conversation_preferences p ON p.room=h.room AND p.subject=? WHERE h.sequence>? AND h.sender<>? AND h.type IN ('m.room.message','m.room.encrypted') AND h.received_ms>? AND coalesce(p.archived,0)=0 AND coalesce(p.muted,0)=0 AND h.sequence>coalesce(p.read_sequence,0) AND h.redacts IS NULL AND NOT EXISTS(SELECT 1 FROM history_receipts r WHERE r.room=h.room AND r.redacts=h.event_id) ORDER BY h.sequence LIMIT 200",(who['subject'],after,who['mxid'],int((time.time()-86400)*1000))).fetchall()
        with connect(s.ledger) as c:
            live={r['room'] for r in c.execute("SELECT room FROM yichat_memberships WHERE mxid=? AND membership='join' AND pending_event IS NULL",(who['mxid'],))}
            ended={r['room'] for r in c.execute('SELECT room FROM yichat_group_lifecycle WHERE dissolved_ms IS NOT NULL')}
        rooms={};cursor=after;stopped=False
        for row in records:
            rid=row['room']
            if rid in live and rid not in ended:
                if rid not in rooms and len(rooms)>=20:stopped=True;break
                old=rooms.get(rid)
                rooms[rid]={'room':rid,'sequence':str(row['sequence']),'event':row['event'],'count':(old['count'] if old else 0)+1}
            cursor=row['sequence']
        more=stopped or len(records)==200
        return {'cursor':str(cursor if more else high),'rooms':list(rooms.values()),'has_more':more}
    @app.get('/api/transactions')
    async def transactions(request:Request,before:int=9223372036854775807):
        who=session(request)
        if not 0<before<=9223372036854775807:fail(400,'无效交易游标')
        with connect(s.ledger) as c:
            # Only operations whose order/payout is actually this user's asset operation.
            rows=c.execute('''SELECT t.*,o.kind order_kind,'send' action FROM yidt_transactions t JOIN yichat_orders o ON o.deposit=t.hash JOIN yichat_order_participants i ON i.order_id=o.id WHERE i.sender_uid=? AND t.id<?
            UNION ALL SELECT t.*,o.kind order_kind,p.kind action FROM yidt_transactions t JOIN yichat_payouts p ON p.tx=t.hash JOIN yichat_payout_identities i ON i.payout_id=p.id JOIN yichat_orders o ON o.id=p.order_id WHERE i.uid=? AND t.id<? ORDER BY id DESC LIMIT 31''',(who['subject'],before,who['subject'],before)).fetchall()
        items=[]
        for row in rows[:30]:
            r=dict(row);items.append({'id':r['id'],'hash':r['hash'],'created':r['created'],'source':r['source'],'destination':r['destination'],'amount':app.state.funds.y.fmt(r['credit']),'gas':app.state.funds.y.fmt(r['gas']),'action':('发送转账' if r['order_kind']=='direct' else '发送红包') if r['action']=='send' else '退款到账' if r['action']=='refund' else '确认收款' if r['order_kind']=='direct' else '领取红包','url':'https://yidt.yitiao.org/explorer/tx/'+r['hash']})
        return {'items':items,'next_before':items[-1]['id'] if len(rows)>30 else None}
