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
    rules=[('yidt.yitiao.org',r'/explorer/tx/([a-f0-9]{64})','transaction'),('yitiao.org',r'/ycards/card/([a-f0-9]{32})/([A-Za-z0-9_-]{1,100})/','card'),('yitiao.org',r'/games/parachute/share/([A-Za-z0-9_-]{32})/','game'),('chat.yitiao.org',r'/orders/([A-Za-z0-9_-]{32})','order')]
    for host,pattern,kind in rules:
        m=re.fullmatch(pattern,u.path)
        if u.netloc==host and m:return kind,m.groups()
    return None

def metadata(settings,url):
    parsed=parse(url)
    if not parsed:return None
    kind,ids=parsed
    with connect(settings.ledger) as c:
        if kind=='transaction':
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
