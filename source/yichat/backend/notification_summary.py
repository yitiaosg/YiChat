"""Bounded, user-visible FCM content. No URLs, media bytes or credentials in summaries."""
import re
from . import previews

MAX_PREVIEW_BYTES=1200
URL=re.compile(r'https://[^\s<>"\u3000]+')
TRAILING='，。！？、；：）】》」』.,!?;:)'


def summary(content,kind='private'):
    if not isinstance(content,dict):return '你有新的消息'
    group=kind=='group'
    def say(action):return ('' if group else '对方')+action
    body=content.get('body') if isinstance(content.get('body'),str) else ''
    share=content.get('org.yitiao.share');share=share if isinstance(share,dict) else {}
    business=content.get('org.yitiao.business');business=business if isinstance(business,dict) else {}
    urls=[u.rstrip(TRAILING) for u in URL.findall(body)]
    parsed=[previews.parse(u) for u in urls];parsed=[p for p in parsed if p]
    order=business.get('kind')=='order' or any(p[0]=='order' for p in parsed)
    gift_url=str(share.get('url') or body).strip()
    gift=bool(re.fullmatch(r'https://chat\.yitiao\.org/gifts/[a-f0-9]{32}',gift_url)) or any(re.fullmatch('[a-f0-9]{32}',str(party.get('id',''))) and party.get(key)=='gift' for party,key in [(business,'kind'),(share,'type')])
    if share.get('type')=='receipt' and order:return say('已确认收款')
    if gift:return say('赠送了一张卡片' if group else '向你赠送了一张卡片')
    if order:return say('发送了一个红包' if group else '向你转了一笔账')
    media={'m.image':'发送了一张图片','m.audio':'发送了一条语音','m.video':'发送了一段视频','m.file':'发送了一个文件','m.location':'分享了一个位置'}
    if content.get('msgtype') in media:return say(media[content['msgtype']])
    if parsed:
        typ=parsed[0][0]
        if typ=='card':return say('分享了一张卡片')
        if typ=='transaction':return say('分享了一笔 YIDT 交易')
        if typ=='doudizhu':return say('分享了斗地主房间' if group else '邀请你加入斗地主房间')
        if typ in ('doudizhu_record','game'):return say('分享了一条游戏记录')
    return body[:2000] if body else say('发送了一条消息')


def bounded(text):
    if not isinstance(text,str):return ''
    text=''.join(c for c in text if c>=' ' or c in '\n\t').strip()
    data=text.encode('utf-8')
    return text if len(data)<=MAX_PREVIEW_BYTES else data[:MAX_PREVIEW_BYTES-3].decode('utf-8',errors='ignore')+'…'


def event_preview(settings,who,hint):
    """Read only hash-verified, still-unread accepted messages for this recipient."""
    import hashlib,json
    from . import archive
    from .db import connect
    from .conversations import unread_notification_events
    if hint['event'] not in unread_notification_events(settings,who,hint['room'],hint['event']):return ''
    with connect(settings.db) as c:
        receipt=c.execute('SELECT sha256,type FROM history_receipts WHERE event_id=? AND room=?',(hint['event'],hint['room'])).fetchone()
    if not receipt or receipt['type']!='m.room.message':return ''
    with archive.connect(settings.archive_reader) as c:
        row=c.execute('SELECT body FROM events WHERE event_id=%s AND room=%s',(hint['event'],hint['room'])).fetchone()
    if not row:return ''
    raw=bytes(row['body'])
    if hashlib.sha256(raw).hexdigest()!=receipt['sha256']:raise ValueError('Push source integrity mismatch')
    event=json.loads(raw)
    if event.get('event_id')!=hint['event'] or event.get('room_id')!=hint['room'] or event.get('type')!='m.room.message':raise ValueError('Push source identity mismatch')
    return bounded(summary(event.get('content'), 'group' if hint['kind']=='group' else 'private'))
