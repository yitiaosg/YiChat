"""First-party login sessions. Revocation keeps chat keys and local archives."""
import secrets,time
from datetime import datetime,timezone
from flask import request,session,redirect,abort

def install(app,connect):
    with connect() as c:c.execute('CREATE TABLE IF NOT EXISTS account_login_devices(id TEXT UNIQUE NOT NULL,sid TEXT PRIMARY KEY,label TEXT,created REAL,last_seen REAL)')
    def label():
        ua=request.headers.get('User-Agent','')
        platform=next((name for mark,name in [('Android','Android'),('iPhone','iPhone'),('iPad','iPad'),('Windows','Windows'),('Macintosh','Mac'),('Linux','Linux')] if mark in ua),'其他系统')
        browser=next((name for mark,name in [('Edg/','Edge'),('Firefox/','Firefox'),('Chrome/','Chrome / WebView'),('Safari/','Safari')] if mark in ua),'浏览器')
        return platform+' · '+browser
    @app.before_request
    def observe():
        user=session.get('user');sid=session.get('yidt_sid');now=time.time()
        if not user or not sid or session.get('_devices_seen_at',0)>now-300:return
        with connect() as c:
            live=c.execute('SELECT 1 FROM yidt_sessions WHERE sid=? AND user=? AND revoked=0 AND expires>?',(sid,user,now)).fetchone()
            if not live:return
            c.execute('INSERT INTO account_login_devices VALUES(?,?,?,?,?) ON CONFLICT(sid) DO UPDATE SET label=excluded.label,last_seen=excluded.last_seen', (secrets.token_hex(16),sid,label(),now,now))
        session['_devices_seen_at']=now
    @app.context_processor
    def context():
        if request.endpoint!='account' or not session.get('user'):return {}
        now=time.time();user=session['user'];sid=session.get('yidt_sid')
        with connect() as c:
            # Older sessions retain their authorization; never invent device data.
            for row in c.execute('SELECT sid FROM yidt_sessions WHERE user=? AND revoked=0 AND expires>?',(user,now)).fetchall():
                c.execute('INSERT OR IGNORE INTO account_login_devices(id,sid) VALUES(?,?)',(secrets.token_hex(16),row['sid']))
            rows=c.execute('SELECT m.id,m.label,m.created,m.last_seen,s.sid,s.expires FROM yidt_sessions s JOIN account_login_devices m USING(sid) WHERE s.user=? AND s.revoked=0 AND s.expires>? ORDER BY (s.sid=?) DESC,m.last_seen DESC LIMIT 100',(user,now,sid)).fetchall()
        def stamp(at):return datetime.fromtimestamp(at,timezone.utc).isoformat() if at else None
        devices=[dict(id=r['id'],label=r['label'] or '早期登录设备（未记录设备信息）',current=r['sid']==sid,last_seen=stamp(r['last_seen']),expires=stamp(r['expires'])) for r in rows]
        return {'login_devices':devices,'devices_csrf':session.setdefault('_devices_csrf',secrets.token_urlsafe(32))}
    @app.post('/account/devices/revoke/')
    def revoke():
        user=session.get('user');sid=session.get('yidt_sid')
        if not user or not sid:abort(401)
        origin=request.headers.get('Origin')
        if origin and origin!=request.host_url.rstrip('/'):abort(403)
        token=request.form.get('csrf','')
        if not token or not secrets.compare_digest(token,session.get('_devices_csrf','')):abort(403)
        target=request.form.get('device','');own=False
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM yidt_sessions WHERE sid=? AND user=? AND revoked=0 AND expires>?',(sid,user,time.time())).fetchone():abort(401)
            if target=='others':c.execute('UPDATE yidt_sessions SET revoked=1 WHERE user=? AND sid<>?',(user,sid))
            else:
                row=c.execute('SELECT s.sid FROM yidt_sessions s JOIN account_login_devices m USING(sid) WHERE s.user=? AND m.id=?',(user,target)).fetchone()
                if not row:abort(404)
                c.execute('UPDATE yidt_sessions SET revoked=1 WHERE sid=? AND user=?',(row['sid'],user));own=row['sid']==sid
        if own:session.clear();return redirect('/login/?next=/account/')
        return redirect('/account/security/?ok=已退出所选设备，对应YiChat会话会自动锁定')
