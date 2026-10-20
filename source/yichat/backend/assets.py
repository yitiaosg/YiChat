"""Explicit YCard gifts. A message card never authorizes an asset mutation."""
import json
import re
import secrets
import time
from fastapi import Request
from .db import connect,transaction
from .security import fail,valid_sid
from . import policy,membership

SCHEMA='''CREATE TABLE IF NOT EXISTS yichat_card_gifts(
 id TEXT PRIMARY KEY,sender_uid TEXT NOT NULL,recipient_uid TEXT NOT NULL,
 sender TEXT NOT NULL,recipient TEXT NOT NULL,room TEXT NOT NULL,instance TEXT NOT NULL,
 request_key TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,
 UNIQUE(sender_uid,request_key));
CREATE TABLE IF NOT EXISTS yichat_gift_renderings(id TEXT PRIMARY KEY REFERENCES yichat_card_gifts(id),payload TEXT NOT NULL);'''


def gift(s,who,info,data):
    iid=data.get('instance','');key=data.get('key','')
    if not isinstance(iid,str) or not re.fullmatch('[a-f0-9]{32}',iid):fail(400,'无效卡片 ID')
    if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{16,100}',key):fail(400,'需要有效的幂等键')
    recipient=policy.identity(s,str(data.get('recipient','')))
    if recipient['uid']==who['subject']:fail(400,'不能赠送给自己')
    if info['kind']=='direct' and policy.mxid(s,recipient['username']) not in (info['owner'],info['peer']):fail(403,'接收者不是私聊参与者')
    payload=json.dumps([info['room'],iid,recipient['uid']],separators=(',',':'))
    with transaction(s.ledger) as c:
        if not valid_sid(s,who['sid'],who['username']):fail(401,'主站会话已退出')
        policy.require_send(s,info,who['mxid'])
        membership.require(c,info['room'],who['mxid'])
        membership.require(c,info['room'],policy.mxid(s,recipient['username']))
        old=c.execute('SELECT * FROM yichat_card_gifts WHERE sender_uid=? AND request_key=?',(who['subject'],key)).fetchone()
        if old:
            if old['payload']!=payload:fail(409,'幂等键已用于不同赠送')
            return {'id':old['id'],'instance':old['instance'],'recipient_uid':old['recipient_uid'],'state':'transferred'}
        card=c.execute('SELECT owner FROM yc_instances WHERE id=?',(iid,)).fetchone()
        if not card or card['owner']!=who['username']:fail(403,'你不是卡片当前持有人')
        checks=[('yc_destroyed','SELECT 1 FROM yc_destroyed WHERE instance=?'),
                ('yc_listings',"SELECT 1 FROM yc_listings WHERE instance=? AND state='active'"),
                ('yc_auctions',"SELECT 1 FROM yc_auctions WHERE instance=? AND state='active'")]
        for table,sql in checks:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():fail(503,'YCards 数据结构不完整，赠送已禁用')
            if c.execute(sql,(iid,)).fetchone():fail(409,'卡片已销毁、挂售或拍卖，当前不可转让')
        from .card_reads import store
        rendering=store(s).preview(iid,who['username'],connection=c)
        gid=secrets.token_hex(16);now=time.time()
        n=c.execute('UPDATE yc_instances SET owner=?,acquired=? WHERE id=? AND owner=?',(recipient['username'],now,iid,who['username'])).rowcount
        if n!=1:fail(409,'卡片所有权已变化')
        c.execute('INSERT INTO yichat_card_gifts VALUES(?,?,?,?,?,?,?,?,?,?)',(gid,who['subject'],recipient['uid'],who['username'],recipient['username'],info['room'],iid,key,payload,now))
        c.execute('INSERT INTO yichat_gift_renderings VALUES(?,?)',(gid,json.dumps(rendering,ensure_ascii=False,separators=(',',':'))))
        return {'id':gid,'instance':iid,'recipient_uid':recipient['uid'],'state':'transferred'}


def migrate(s):
    with connect(s.ledger) as c:c.executescript(SCHEMA)


def install(app,s,session,room_access,require_send):
    @app.get('/api/cards/gifts/by-key/{key}')
    async def gift_result(request:Request,key:str):
        who=session(request)
        with connect(s.ledger) as c:
            row=c.execute('SELECT id,instance,recipient_uid,recipient,room FROM yichat_card_gifts WHERE sender_uid=? AND request_key=?',(who['subject'],key)).fetchone()
        if not row:fail(404,'赠送记录不存在')
        return dict(row)|{'state':'transferred'}

    @app.get('/api/cards/gifts/{gid}')
    async def gift_preview(request:Request,gid:str,room:str):
        who,info=await room_access(request,room)
        with connect(s.ledger) as c:
            row=c.execute('SELECT id,room,instance,sender,recipient,created FROM yichat_card_gifts WHERE id=? AND room=?',(gid,room)).fetchone()
            if not row:fail(404,'赠卡记录不存在或不属于此会话')
            if who['username'] not in (row['sender'],row['recipient']):
                from . import archive
                at=int(row['created']*1000)
                with archive.connect(s.archive_reader) as pg:
                    if not archive.allowed(pg,room,who['mxid'],at,at+1):fail(403,'没有赠卡发生时的会话历史权限')
            snapshot=c.execute('SELECT payload FROM yichat_gift_renderings WHERE id=?',(gid,)).fetchone()
            if snapshot:render=json.loads(snapshot['payload'])
            else:
                from .card_reads import store
                # Older gifts can only use data still visible to the recipient.
                try:render=store(s).preview(row['instance'],row['recipient'],connection=c)
                except Exception:fail(404,'历史赠卡缺少可验证的卡面资源')
        return dict(row)|{'state':'transferred','card':render,'snapshot':bool(snapshot)}

    @app.post('/api/cards/gifts')
    async def create_gift(request:Request):
        data=await request.json();who,info=await room_access(request,str(data.get('room','')))
        require_send(who,info)
        # Group recipients must also be actual current members.
        target=policy.identity(s,str(data.get('recipient','')))
        await app.state.matrix.member(who,info['room'],policy.mxid(s,target['username']))
        return gift(s,who,info,data)
