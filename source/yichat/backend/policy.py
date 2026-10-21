"""Shared server-maintained room registry and live account privacy checks."""
import sys
import secrets
import time
from pathlib import Path
from .db import connect, transaction
from .security import fail, digest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import account_social as social


def migrate_identities(s):
    social.migrate(s.accounts)
    with social.database(s.accounts) as accounts, transaction(s.db) as c:
        for row in c.execute('SELECT * FROM identities').fetchall():
            person=accounts.execute('SELECT uid,username FROM account_ids WHERE uid=?',(row['subject'],)).fetchone()
            if not person:person=accounts.execute('SELECT uid,username FROM account_ids WHERE username=?',(row['username'],)).fetchone()
            if not person:raise RuntimeError('Existing Matrix identity has no stable account mapping')
            uid=person['uid']
            if row['subject']!=uid:
                # Keep the original MXID, device keys, rooms and backup identity.
                c.execute('UPDATE sessions SET subject=? WHERE subject=?',(uid,row['subject']))
                c.execute('UPDATE files SET uploader=? WHERE uploader=?',(uid,row['subject']))
                c.execute('UPDATE identities SET subject=? WHERE subject=?',(uid,row['subject']))
            c.execute('UPDATE identities SET username=? WHERE subject=?',(person['username'],uid))


def identity(s,username):
    try:return social.get_identity(s.accounts,username=username)
    except social.PolicyError as e:fail(e.status,{'errcode':'M_FORBIDDEN','error':str(e),'code':e.code})


def historical_uid(s,username):
    with social.database(s.accounts) as c:row=c.execute('SELECT uid FROM account_ids WHERE username=?',(username,)).fetchone()
    if not row:raise RuntimeError('Historical asset record has no stable user ID')
    return row['uid']


def username(s,uid):
    try:return social.get_identity(s.accounts,uid=uid)['username']
    except social.PolicyError as e:fail(e.status,{'errcode':'M_FORBIDDEN','error':str(e),'code':e.code})


def mxid(s,username):
    who=identity(s,username)
    with connect(s.db) as c:row=c.execute('SELECT mxid FROM identities WHERE subject=?',(who['uid'],)).fetchone()
    return row['mxid'] if row else '@u_'+digest(who['uid'])+':'+s.server_name


def subject(s,matrix_id):
    with connect(s.db) as c:row=c.execute('SELECT subject FROM identities WHERE mxid=?',(matrix_id,)).fetchone()
    if not row:fail(403,'未知 Matrix 用户')
    return row['subject']


def require_send(s,info,sender):
    if info['kind']!='direct':
        from .groups import require_send as group_send
        group_send(s,info.get('room',''),sender);return
    if sender not in (info['owner'],info['peer']):fail(403,'私聊身份不匹配')
    peer=info['peer'] if sender==info['owner'] else info['owner']
    require_uid_send(s,subject(s,sender),subject(s,peer))


def require_uid_send(s,sender_uid,recipient_uid):
    try:social.require_message(s.accounts,sender_uid,recipient_uid)
    except social.PolicyError as e:fail(e.status,{'errcode':'M_FORBIDDEN','error':str(e),'code':e.code})


def issue_ticket(s,owner,kind,peer=None):
    token=secrets.token_urlsafe(32)
    with transaction(s.db) as c:
        c.execute('INSERT INTO room_tickets VALUES(?,?,?,?,?,NULL)',(token,owner,kind,peer,time.time()+120))
    return token


def validate_ticket(s,token,owner):
    with connect(s.db) as c:t=c.execute('SELECT * FROM room_tickets WHERE token=? AND expires>?',(token,time.time())).fetchone()
    if not t or t['owner']!=owner:fail(403,'会话必须通过 YiChat 服务创建')
    return dict(t)


def register_room(s,token,room):
    with transaction(s.db) as c:
        t=c.execute('SELECT * FROM room_tickets WHERE token=? AND expires>?',(token,time.time())).fetchone()
        if not t or (t['room'] and t['room']!=room):fail(403,'会话票据无效或已使用')
        c.execute('INSERT OR IGNORE INTO rooms VALUES(?,?,?,?,30)',(room,t['kind'],t['owner'],t['peer']))
        c.execute('INSERT OR IGNORE INTO room_modes VALUES(?,?,?)',(room,'direct' if t['kind']=='direct' else 'plain',int(time.time()*1000)))
        if t['kind']=='group':
            from .groups import register
            register(c,room)
        else:c.execute('INSERT OR IGNORE INTO direct_visibility VALUES(?,0)',(room,))
        c.execute('UPDATE room_tickets SET room=? WHERE token=?',(room,token))


def room(s,room_id):
    with connect(s.db) as c:
        r=c.execute('SELECT r.*,m.encryption FROM rooms r JOIN room_modes m USING(room) WHERE room=?',(room_id,)).fetchone()
    if not r:fail(403,'非 YiChat 会话')
    return dict(r)


def validate_event(s,info,event_type,content,sender,state_key=None,control=None):
    if event_type=='m.room.member':
        membership=content.get('membership')
        if info['kind']=='direct':
            if state_key not in (info['owner'],info['peer']):fail(403,'私聊参与者固定')
            if membership in ('invite','join'):
                # Joining does not send content, but an invite is a sender action.
                if membership=='invite':require_send(s,info,sender)
            elif membership not in ('leave',):fail(403,'私聊成员固定')
            elif sender!=state_key:fail(403,'私聊只能由本人离开')
        else:
            from .groups import validate_member
            validate_member(s,info,sender,state_key,content,control)
        return
    if event_type=='m.room.encryption':
        if info['encryption']=='plain':fail(403,'新群不启用端到端加密')
        if content.get('algorithm')!='m.megolm.v1.aes-sha2':fail(403,'不支持此加密算法')
        return
    if event_type=='m.room.history_visibility' and content.get('history_visibility')!='joined':
        fail(403,'历史可见性固定为 joined')
    if event_type=='m.room.guest_access' and content.get('guest_access')!='forbidden':
        fail(403,'访客加入已关闭')
    if event_type in ('m.room.create','m.room.history_visibility','m.room.join_rules','m.room.power_levels','m.room.name','m.room.topic'):
        return
    if state_key is None:
        if not control:require_send(s,info,sender)
        if info['encryption'] in ('direct','legacy_encrypted') and event_type not in ('m.room.encrypted','m.room.redaction','m.reaction'):
            fail(403,'加密会话禁止明文消息')
        if info['encryption']=='plain' and event_type=='m.room.encrypted':fail(403,'新群只接收服务器可读事件')
        if event_type=='m.room.encrypted':
            import json
            if len(json.dumps(content,ensure_ascii=False,separators=(',',':')).encode())>65536:fail(413,'密文事件超过 65536 字节')
        elif event_type=='m.room.message':
            # Python length counts Unicode code points, including non-BMP characters.
            for value in (content, content.get('m.new_content',{})):
                if not isinstance(value,dict):fail(400,'无效编辑内容')
                body=value.get('body','')
                if not isinstance(body,str) or len(body)>2000:fail(400,'文字最多 2000 个 Unicode 码点')


def admit_attachments(s,event_id,room,sender,event_type,content,worker,boot):
    ids=content.get('org.yitiao.attachments',[])
    if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(x,str) or len(x)!=48 or any(ch not in '0123456789abcdef' for ch in x) for x in ids):fail(400,'无效附件关联清单')
    if not ids:return
    if event_type not in ('m.room.encrypted','m.room.message'):fail(403,'只有会话消息可以关联附件')
    uid=subject(s,sender)
    with transaction(s.db) as c:
        for fid in set(ids):
            row=c.execute('SELECT f.*,h.sha256 FROM files f JOIN file_hashes h USING(id) WHERE f.id=?',(fid,)).fetchone()
            if not row or row['room']!=room or row['uploader']!=uid or row['removed'] or row['expires']<=time.time():fail(403,'附件未上传、已到期或不属于此发送者及会话')
            if not (s.objects/fid).is_file():fail(503,'附件实体缺失，发送已暂停')
            c.execute('INSERT OR IGNORE INTO attachment_admissions VALUES(?,?,?,?)',(event_id,fid,worker,boot))


def finish_attachments(s,event_id,received_ms):
    expiry=(received_ms//86400000+31)*86400
    with transaction(s.db) as c:
        c.execute('UPDATE files SET expires=max(expires,?) WHERE id IN (SELECT file FROM attachment_admissions WHERE event=?)',(expiry,event_id))
        c.execute('DELETE FROM attachment_admissions WHERE event=?',(event_id,))


def validate_to_device(s,sender,event_type,data):
    """Protocol control plane only; future clients never render it as a timeline.
    Encrypted Olm content remains opaque and is not treated as a user message.
    """
    allowed={'m.room.encrypted','m.room_key_request','m.secret.request','m.dummy',
        *('m.key.verification.'+name for name in ('request','start','ready','accept','key','mac','done','cancel'))}
    if event_type not in allowed:fail(403,'设备间接口仅允许 Matrix 加密与验证信令')
    messages=data.get('messages',{})
    if not isinstance(messages,dict) or len(messages)>100:fail(400,'无效设备消息')
    sender_uid=subject(s,sender)
    for recipient,devices in messages.items():
        recipient_uid=subject(s,recipient)
        if not isinstance(devices,dict):fail(400,'无效设备消息')
        if recipient!=sender:
            with social.database(s.accounts) as c:permitted=social.can_message(c,sender_uid,recipient_uid)
            if not permitted:
                # Existing E2EE rooms still need key transport for authorized
                # history and shared encrypted groups. This cannot create or
                # deliver a timeline message, card, attachment or asset operation.
                with connect(s.ledger) as c:
                    rooms=c.execute("SELECT a.room FROM yichat_memberships a JOIN yichat_memberships b ON a.room=b.room WHERE a.mxid=? AND b.mxid=? AND a.membership='join' AND b.membership='join' AND a.pending_event IS NULL AND b.pending_event IS NULL",(sender,recipient)).fetchall()
                with connect(s.db) as c:
                    common=any(c.execute("SELECT 1 FROM room_modes WHERE room=? AND encryption IN ('direct','legacy_encrypted')",(r['room'],)).fetchone() for r in rooms)
                if event_type!='m.room.encrypted' or not common:
                    fail(403,{'errcode':'M_FORBIDDEN','code':social.PRIVACY_CODE,'error':social.PRIVACY_MESSAGE})
        if event_type=='m.room.encrypted':
            for content in devices.values():
                if not isinstance(content,dict) or content.get('algorithm')!='m.olm.v1.curve25519-aes-sha2' or not isinstance(content.get('ciphertext'),dict):
                    fail(400,'设备间加密必须使用 Matrix Olm 格式')
