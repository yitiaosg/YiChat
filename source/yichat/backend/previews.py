"""Exact route parsing; no generic URL fetcher, DNS, redirects, HTML or private notes."""
from urllib.parse import urlsplit
import re
from .db import connect

def parse(url):
    if not isinstance(url,str) or len(url)>2048: return None
    try:
        u=urlsplit(url)
        if u.port is not None:return None
    except ValueError: return None
    if u.scheme!='https' or u.username or u.password or u.port or u.query or u.fragment or '%' in u.path or '\\' in url: return None
    rules=[('yitiao.org',r'/games/doudizhu/share/([A-Za-z0-9_-]{32})/','doudizhu_record'),('yitiao.org',r'/games/doudizhu/room/(\d{5})/([a-f0-9]{32})/','doudizhu'),('yidt.yitiao.org',r'/explorer/tx/([a-f0-9]{64})','transaction'),('yitiao.org',r'/ycards/card/([a-f0-9]{32})/([A-Za-z0-9_-]{1,100})/','card'),('yitiao.org',r'/games/parachute/share/([A-Za-z0-9_-]{32})/','game'),('chat.yitiao.org',r'/orders/([A-Za-z0-9_-]{32})','order')]
    for host,pattern,kind in rules:
        m=re.fullmatch(pattern,u.path)
        if u.netloc==host and m:return kind,m.groups()
    return None

def metadata(settings,url):
    parsed=parse(url)
    if not parsed:return None
    kind,ids=parsed
    with connect(settings.ledger) as c:
        if kind=='doudizhu_record':
            import json
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='ddz_records'").fetchone():return None
            row=c.execute('SELECT payload FROM ddz_records WHERE share_token=?',ids).fetchone()
            if not row:return None
            record=json.loads(row['payload'])
            return {'type':kind,'title':'斗地主 · 对局记录','url':url,'data':record}
        elif kind=='doudizhu':
            import json
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='ddz_rooms'").fetchone():return None
            row=c.execute('SELECT payload FROM ddz_rooms WHERE code=?',(ids[0],)).fetchone()
            if not row:return None
            r=json.loads(row['payload'])
            if r['id']!=ids[1] or r['mode']!='private':return None
            return {'type':kind,'title':'斗地主 · 房间邀请','url':url,'data':{
                'code':r['code'],'variant':r.get('variant','normal'),'base':r['base'],
                'players':len(r['players']),'status':r['status'],'minimum':r['base']*2000}}
        elif kind=='transaction':
            row=c.execute('SELECT hash,source,destination,credit,gas,created FROM yidt_transactions WHERE hash=?',(ids[0],)).fetchone()
            if row:return {'type':kind,'title':'YIDT 交易','url':url,'data':dict(row)}
        elif kind=='card':
            from .card_reads import public_card
            card=public_card(settings,*ids)
            if card:return {'type':kind,'title':'YCards · '+card['name'],'url':url,'data':card}
        elif kind=='game':
            row=c.execute("SELECT status,exit_height,crash_height,started_at FROM parachute_rounds WHERE share_token=? AND status IN ('jumped','crashed')",ids).fetchone()
            if row:return {'type':kind,'title':'跳伞飞行记录','url':url,'data':dict(row)|{'snapshot':True,'render_version':'parachute-scene-v1'}}
    return None
