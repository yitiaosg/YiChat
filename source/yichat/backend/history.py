"""Authenticated packet catch-up and bounded, ephemeral peer relay.

Hashes come from original accepted source events, never from a peer. The relay
cannot create Matrix events, upload keys, alter archives or move assets.
"""
import base64
import hashlib
import json
import secrets
import re
import time
from fastapi import Request
from . import archive, policy, membership
from .db import connect, transaction
from .security import fail

TYPES=('m.room.message','m.room.encrypted','m.room.redaction')
BUSINESS='org.yitiao.business'


def business_reference(s,room,sender,content):
    ref=content.get(BUSINESS)
    if ref is None:
        match=re.search(r'https://chat\.yitiao\.org/orders/([A-Za-z0-9_-]{32})(?![A-Za-z0-9_-])',str(content.get('body','')))
        if not match:return None
        with connect(s.ledger) as c:row=c.execute('SELECT id FROM yichat_orders WHERE id=? AND room=?',(match[1],room)).fetchone()
        return 'order:'+match[1] if row else None
    if not isinstance(ref,dict) or set(ref)!={'kind','id'} or ref.get('kind') not in ('order','gift') or not isinstance(ref.get('id'),str):fail(400,'无效业务消息关联')
    with connect(s.ledger) as c:
        if ref['kind']=='order':
            row=c.execute('SELECT room,user sender,deposit FROM yichat_orders WHERE id=?',(ref['id'],)).fetchone()
            valid=bool(row)
        else:
            row=c.execute('SELECT room,sender FROM yichat_card_gifts WHERE id=?',(ref['id'],)).fetchone();valid=bool(row)
    if not valid or row['room']!=room:fail(403,'业务消息只能关联此会话的真实订单或赠卡记录')
    return ref['kind']+':'+ref['id']


def record_rows(s,rows):
    """Keep metadata/hash only. Exact body matches archive.canonical, including LF."""
    records=[]
    for eid,room,seq,received,raw in rows:
        event=json.loads(raw)
        if event.get('type') not in TYPES:continue
        event.pop('unsigned',None);event.update(event_id=eid,room_id=room)
        body=archive.canonical(event);target=event.get('content',{}).get('redacts') or event.get('redacts')
        ref=event.get('content',{}).get(BUSINESS)
        if not ref and event['type']=='m.room.message':ref=business_reference(s,room,event['sender'],event.get('content',{}))
        records.append((eid,room,seq,received,event['sender'],event['type'],event.get('origin_server_ts',received),hashlib.sha256(body).hexdigest(),target if event['type']=='m.room.redaction' else None,json.dumps(ref) if ref else None))
    with transaction(s.db) as c:
        for record in records:
            old=c.execute('SELECT sha256 FROM history_receipts WHERE event_id=?',(record[0],)).fetchone()
            if old and old['sha256']!=record[7]:raise RuntimeError('Immutable packet receipt changed')
            c.execute('INSERT OR IGNORE INTO history_receipts VALUES(?,?,?,?,?,?,?,?,?,?)',record)


def backfill(s,limit=500):
    with connect(s.db) as c:cursor=c.execute('SELECT value FROM history_meta WHERE key=\'backfill\'').fetchone()
    after=int(cursor['value']) if cursor else 0
    with archive.connect(s.archive_reader) as pg:
        rows=pg.execute('SELECT event_id,room,sequence,received_ms,body FROM events WHERE sequence>%s ORDER BY sequence LIMIT %s',(after,limit)).fetchall()
    if rows:
        record_rows(s,[(r['event_id'],r['room'],r['sequence'],r['received_ms'],bytes(r['body'])) for r in rows])
        with transaction(s.db) as c:c.execute("INSERT INTO history_meta VALUES('backfill',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(rows[-1]['sequence']),))
    if len(rows)<limit:
        with transaction(s.db) as c:c.execute("INSERT OR REPLACE INTO history_meta VALUES('backfill_ready','1')")
    return len(rows)


def current_rooms(s,who):
    with connect(s.ledger) as c:
        return {r['room'] for r in c.execute("SELECT room FROM yichat_memberships WHERE mxid=? AND membership='join' AND pending_event IS NULL",(who['mxid'],))}


def accessible(pg,rooms,who,row,s=None):
    if row['room'] not in rooms:return False
    if archive.allowed(pg,row['room'],who['mxid'],row['received_ms'],row['received_ms']+1):return True
    # A returning member who could see the original must also learn that it
    # was withdrawn while they were away. No reason/body is exposed here.
    if s and dict(row).get('redacts'):
        with connect(s.db) as c:original=c.execute('SELECT * FROM history_receipts WHERE room=? AND event_id=?',(row['room'],row['redacts'])).fetchone()
        return bool(original and archive.allowed(pg,original['room'],who['mxid'],original['received_ms'],original['received_ms']+1))
    return False


def deleted(pg,row):
    # Absence is not proof of retention cleanup. Never ask peers for server-held
    # or temporarily unavailable content, including outages/ingestion lag.
    if archive.utcday(row['received_ms']).toordinal()+31>archive.utcday(int(time.time()*1000)).toordinal():return False
    states=pg.execute('SELECT cleaned_at FROM archive_audit WHERE room=%s AND day=%s AND version=1',(row['room'],archive.utcday(row['received_ms']))).fetchall()
    return len(states)==2 and all(r['cleaned_at'] for r in states)


def tombstone(c,row):
    red=c.execute('SELECT * FROM history_receipts WHERE room=? AND redacts=? ORDER BY sequence LIMIT 1',(row['room'],row['event_id'])).fetchone()
    if not red:return None
    # This is an authenticated view/tombstone, not a reconstructed original packet.
    return {'event':{'event_id':row['event_id'],'room_id':row['room'],'sender':row['sender'],'type':row['type'],'origin_server_ts':row['origin_server_ts'],'content':{}},'redaction':{'event_id':red['event_id'],'room_id':red['room'],'sender':red['sender'],'type':'m.room.redaction','origin_server_ts':red['origin_server_ts'],'content':{'redacts':row['event_id']},'redacts':row['event_id']}}


class Relay:
    def __init__(self,s):self.s=s;self.pending={}
    def prune(self):
        now=time.monotonic()
        for key in list(self.pending):
            if self.pending[key]['expires']<now:del self.pending[key]
    def want(self,who,row):
        self.prune()
        for key,r in self.pending.items():
            if r['requester']==who['token_hash'] and r['event_id']==row['event_id']:return key
        if len(self.pending)>=256 or sum(r['requester']==who['token_hash'] for r in self.pending.values())>=64:fail(429,'历史补齐队列已满，请稍后重试')
        key=secrets.token_urlsafe(24)
        self.pending[key]={'requester':who['token_hash'],'mxid':who['mxid'],'subject':who['subject'],'expires':time.monotonic()+120,'raw':None,**dict(row)}
        return key


def install(app,s,session):
    relay=Relay(s);app.state.history_relay=relay

    def app_transport(request):
        # Product scope: message backup/recovery is for native Apps. Real
        # browser requests (including old loaded Web builds) cannot participate.
        # Bearer/no-Origin is a transport requirement, not proof of App identity;
        # all existing account, room and historical permissions still apply.
        if 'origin' in request.headers or 'sec-fetch-mode' in request.headers or not request.headers.get('authorization','').startswith('Bearer '):
            fail(403,{'code':'YICHAT_APP_ONLY','error':'消息数据包备份与成员恢复仅供原生 App 使用；Web 仅同步服务器撤回状态'})

    @app.get('/api/history/updates')
    async def updates(request:Request,since:int=0,after:int=0,until:int=0):
        who=session(request);rooms=current_rooms(s,who)
        if since<0 or after<0 or until<0:fail(400,'无效同步游标')
        with archive.connect(s.archive_reader) as pg:
            cp=pg.execute("SELECT * FROM checkpoints WHERE source='synapse'").fetchone()
            if not cp or not cp['healthy'] or cp['observed_ms']<int(time.time()*1000)-120000:fail(503,'历史存证正在同步，请稍后重试')
            end=min(until or cp['observed_ms'],cp['observed_ms'])
            with connect(s.db) as c:
                if not c.execute("SELECT 1 FROM history_meta WHERE key='backfill_ready'").fetchone():fail(503,'历史摘要正在回填，请稍后重试')
                rows=c.execute('SELECT * FROM history_receipts WHERE room IN ('+','.join('?' for _ in rooms)+') AND received_ms>=? AND received_ms<=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT 100',(*sorted(rooms),since,end,after,cp['sequence'])).fetchall() if rooms else []
                result=[]
                for row in rows:
                    if accessible(pg,rooms,who,row,s):result.append(dict(row)|{'tombstone':tombstone(c,row)})
        return {'records':result,'until':end,'next_after':rows[-1]['sequence'] if len(rows)==100 else None,'scanned_after':rows[-1]['sequence'] if rows else after,'server_ms':int(time.time()*1000),'rooms':sorted(rooms)}

    @app.post('/api/history/packets')
    async def packets(request:Request):
        who=session(request);app_transport(request);data=await request.json();ids=data.get('ids',[])
        if not isinstance(ids,list) or not 1<=len(ids)<=20 or any(not isinstance(i,str) or len(i)>255 for i in ids):fail(400,'每批查询1～20个事件')
        rooms=current_rooms(s,who);result=[]
        with archive.connect(s.archive_reader) as pg,connect(s.db) as c:
            for eid in dict.fromkeys(ids):
                row=c.execute('SELECT * FROM history_receipts WHERE event_id=?',(eid,)).fetchone()
                if not row or not accessible(pg,rooms,who,row,s):fail(403,'没有此事件的历史权限或可信摘要')
                view=tombstone(c,row)
                if row['type']=='m.room.redaction':
                    control={'event_id':eid,'room_id':row['room'],'sender':row['sender'],'type':'m.room.redaction','origin_server_ts':row['origin_server_ts'],'redacts':row['redacts'],'content':{'redacts':row['redacts']}}
                    result.append({'event_id':eid,'tombstone':{'event':control},'source':'server_control'});continue
                if view:result.append({'event_id':eid,'tombstone':view});continue
                source=pg.execute('SELECT body FROM events WHERE event_id=%s AND room=%s',(eid,row['room'])).fetchone()
                if source:
                    if policy.room(s,row['room'])['encryption']!='plain' and row['type']!='m.room.encrypted':fail(503,'加密会话的原事件类型不符，已停止提供数据')
                    raw=bytes(source['body'])
                    if hashlib.sha256(raw).hexdigest()!=row['sha256']:fail(503,'服务端事件存证不一致')
                    result.append({'event_id':eid,'raw':base64.b64encode(raw).decode(),'sha256':row['sha256'],'source':'server'})
                elif deleted(pg,row):result.append({'event_id':eid,'request':relay.want(who,row),'sha256':row['sha256'],'source':'peer_pending'})
                else:fail(503,'服务器事件暂不可用，不能视为已删除或请求其他成员')
        return {'packets':result}

    @app.post('/api/history/exchange')
    async def exchange(request:Request):
        who=session(request);app_transport(request);data=await request.json();offers=data.get('offers',[]);wants=data.get('requests',[])
        if not isinstance(offers,list) or len(offers)>1 or not isinstance(wants,list) or len(wants)>64 or any(not isinstance(k,str) for k in wants):fail(400,'无效历史交换批次')
        relay.prune();rooms=current_rooms(s,who);fulfilled=[];asked=[]
        with archive.connect(s.archive_reader) as pg,connect(s.db) as c:
            for offer in offers:
                if not isinstance(offer,dict):fail(400,'无效数据包')
                item=relay.pending.get(offer.get('request'))
                if not item:continue
                if not accessible(pg,rooms,who,item,s) or who['subject']==item['subject']:fail(403,'只能由其他有权的在线成员补齐')
                if tombstone(c,item):continue
                # Recheck absence immediately before accepting a peer response.
                if pg.execute('SELECT 1 FROM events WHERE event_id=%s',(item['event_id'],)).fetchone() or not deleted(pg,item):fail(409,'服务器仍有该事件，不接受成员补齐')
                try:raw=base64.b64decode(offer.get('raw',''),validate=True)
                except (ValueError,TypeError):fail(400,'无效数据包编码')
                if len(raw)>160000 or hashlib.sha256(raw).hexdigest()!=item['sha256']:fail(422,'数据包哈希校验失败')
                event=json.loads(raw)
                if event.get('event_id')!=item['event_id'] or event.get('room_id')!=item['room']:fail(422,'事件身份不匹配')
                if policy.room(s,item['room'])['encryption']!='plain' and event.get('type')!='m.room.encrypted':fail(422,'私聊只能补齐原生加密数据包')
                if sum(len(r['raw'] or '') for r in relay.pending.values())+len(offer['raw'])>8*1024*1024:fail(429,'补齐暂存已满，请稍后重试')
                item['raw']=offer['raw']
            for key,item in list(relay.pending.items()):
                if item['requester']==who['token_hash'] and key in wants:
                    if not accessible(pg,rooms,who,item,s):continue
                    view=tombstone(c,item)
                    if view or item['raw']:
                        fulfilled.append({'event_id':item['event_id'],'request':key,'raw':None if view else item['raw'],'sha256':item['sha256'],'tombstone':view,'source':'peer'});del relay.pending[key]
                        if len(fulfilled)>=2:break
                elif len(asked)<8 and item['raw'] is None and who['subject']!=item['subject'] and accessible(pg,rooms,who,item,s):
                    if not tombstone(c,item) and deleted(pg,item) and not pg.execute('SELECT 1 FROM events WHERE event_id=%s',(item['event_id'],)).fetchone():
                        asked.append({k:item[k] for k in ('event_id','room','sha256')}|{'request':key})
                        if len(asked)>=8:continue
        return {'fulfilled':fulfilled,'asked':asked,'pending':[k for k in wants if k in relay.pending],'retry_after_ms':20000}
