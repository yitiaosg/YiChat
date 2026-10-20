import hashlib, time, asyncio, math
from fastapi import HTTPException
from .db import connect, transaction

def fail(code, message): raise HTTPException(code, message)
def deny_retry(seconds, message='请求过于频繁，请稍后再试'):
    ms=max(1,math.ceil(seconds*1000))
    raise HTTPException(429,{'errcode':'M_LIMIT_EXCEEDED','error':message,'code':'YICHAT_RATE_LIMITED','retry_after_ms':ms},headers={'Retry-After':str(max(1,math.ceil(seconds)))})

def request_limit(settings, subject, now=None, limit=10):
    """Exact rolling (t-1s,t] window. Burst <=10, no queue or carryover credits."""
    now=time.time() if now is None else now
    with transaction(settings.db) as c:
        c.execute('DELETE FROM request_hits WHERE at<=?',(now-1,))
        rows=c.execute('SELECT at FROM request_hits WHERE subject=? ORDER BY at,id',(subject,)).fetchall()
        if len(rows)>=limit:
            deny_retry(max(0.001,rows[0][0]+1-now))
        c.execute('INSERT INTO request_hits(subject,at) VALUES(?,?)',(subject,now))
def digest(value): return hashlib.sha256(value.encode()).hexdigest()

def valid_sid(settings, sid, username):
    with connect(settings.ledger) as c:
        row = c.execute('SELECT 1 FROM yidt_sessions WHERE sid=? AND user=? AND revoked=0 AND expires>?', (sid, username, time.time())).fetchone()
    return bool(row)

def authenticate(settings, token):
    if not token: fail(401, '请使用主站账号登录')
    with connect(settings.db) as c:
        row = c.execute('SELECT * FROM sessions WHERE token_hash=? AND revoked=0 AND expires>?', (digest(token), time.time())).fetchone()
    if not row: fail(401, '会话已失效')
    with connect(settings.site/'account.db') as c:
        person=c.execute('SELECT username FROM account_ids WHERE uid=?',(row['subject'],)).fetchone()
    if not person or person['username']!=row['username']:fail(401,'账户标识已更新，请重新登录')
    if not valid_sid(settings, row['sid'], row['username']):
        with transaction(settings.db) as c: c.execute('UPDATE sessions SET revoked=1 WHERE sid=?', (row['sid'],))
        fail(401, '主站会话已退出')
    return dict(row)

def check_origin(request, settings):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        if not request.headers.get('origin') and request.headers.get('authorization','').startswith('Bearer '):return
        if request.headers.get('origin') != settings.origin: fail(403, '请求来源不匹配')

def reserve_rate(settings, subject, bucket, size, rate, now=None, max_wait=30):
    """Shared virtual clock, serialized across all workers/devices. No per-connection burst."""
    now = time.time() if now is None else now
    with transaction(settings.db) as c:
        row=c.execute('SELECT next FROM rate WHERE subject=? AND bucket=?',(subject,bucket)).fetchone()
        start=max(now, row[0] if row else now)
        end=start+size/rate
        if end-now > max_wait: deny_retry(end-now)
        c.execute('INSERT INTO rate VALUES(?,?,?) ON CONFLICT(subject,bucket) DO UPDATE SET next=excluded.next',(subject,bucket,end))
    return end-now

async def upload_slot(settings, subject, lease, size):
    now=time.time(); day=int(now//86400)
    with transaction(settings.db) as c:
        c.execute('DELETE FROM upload_leases WHERE expires<?',(now,))
        if c.execute('SELECT COUNT(*) FROM upload_leases WHERE subject=?',(subject,)).fetchone()[0]>=settings.upload_concurrency: deny_retry(1,'上传连接数已达上限')
        used=c.execute('SELECT bytes FROM upload_usage WHERE subject=? AND day=?',(subject,day)).fetchone()
        if (used[0] if used else 0)+size>settings.daily_bytes: deny_retry((day+1)*86400-now,'今日上传额度已用完')
        # Reserve declared size up front. Failed attempts also consume daily ingress quota.
        c.execute('INSERT INTO upload_usage VALUES(?,?,?) ON CONFLICT(subject,day) DO UPDATE SET bytes=bytes+excluded.bytes',(subject,day,size))
        c.execute('INSERT INTO upload_leases VALUES(?,?,?)',(lease,subject,now+60))
