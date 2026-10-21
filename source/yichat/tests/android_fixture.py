"""Explicit opt-in loopback fixture for the Android SDK instrumentation suite.

Run alone, after the other PG suites. Never deploy or expose this application.
"""
import json,os,secrets,threading,time
from pathlib import Path
import httpx,uvicorn
from fastapi import Request
from fastapi.testclient import TestClient
from test_synapse_integration import synapse,free_port
from backend.app import create_app
from backend.db import connect
from backend.security import digest

def test_hold_android_fixture(synapse):
    s,f,users,h,*_=synapse
    port=18018;s.origin='http://127.0.0.1:'+str(port)
    # A real local OIDC server: browser follows login -> signed token/JWKS ->
    # native one-time PKCE exchange. This module is never part of deployment.
    import sys
    from flask import Flask,request as fr,session as fs,redirect
    from urllib.parse import quote
    from werkzeug.serving import make_server
    sys.path.append('/opt/vps-python/lib/python3.11/site-packages')
    import oidc_provider
    s.issuer=s.idp_internal='http://127.0.0.1:18019'
    idp=Flask('android_isolated_oidc');idp.secret_key=secrets.token_urlsafe(32)
    from jinja2 import FileSystemLoader
    idp.static_folder=str(Path(__file__).resolve().parents[2]/'static')
    idp.jinja_loader=FileSystemLoader(str(Path(__file__).resolve().parents[2]/'templates'))
    import account_devices
    account_devices.install(idp,lambda:connect(s.ledger))
    @idp.get('/account/security/')
    @idp.get('/account/')
    def account():
        from flask import render_template
        if not fs.get('user'):return redirect('/login?next=/account/')
        return render_template('account_devices.html')
    oidc_provider.CLIENTS['yichat']={'client_secret':'','redirect_uris':[s.origin+'/auth/callback'],'name':'YiChat'}
    def person(name):
        with connect(s.accounts) as db:
            row=db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
            return dict(row) if row else None
    oidc_provider.init_oidc(idp,oidc_provider.OIDCDeps(issuer=s.issuer,db_conn=lambda:connect(s.accounts),get_user=person,
        current_username=lambda:fs.get('user'),claims_for=lambda user:{'sub':user['username'],'user_id':users[user['username']]['subject'],'preferred_username':user['username']},
        login_redirect=lambda nxt:redirect('/login?next='+quote(nxt,safe='')),deny_page=lambda title,message:(message,403),key_path=s.root/'android-fixture-oidc.pem'))
    @idp.route('/login',methods=['GET','POST'])
    def fixture_login():
        if fr.method=='POST':
            if fr.form.get('username')!='alice' or fr.form.get('password')!='IsolatedPassword123!':return 'Invalid fixture credentials',403
            sid=secrets.token_urlsafe(32)
            with connect(s.ledger) as db:db.execute('INSERT INTO yidt_sessions VALUES(?,?,?,0)',(sid,'alice',time.time()+3600))
            fs.update(user='alice',yidt_sid=sid)
            return redirect(fr.args.get('next','/'))
        return '<!doctype html><meta name="viewport" content="width=device-width"><title>Isolated YiChat login</title><h1>Isolated test login</h1><form method="post"><label>Username<input name="username" autocomplete="username"></label><label>Password<input name="password" type="password" autocomplete="current-password"></label><button>Sign in</button></form>'
    idp_server=make_server('127.0.0.1',18019,idp,threaded=True)
    idp_thread=threading.Thread(target=idp_server.serve_forever,daemon=True);idp_thread.start()
    app=create_app(s);accounts={}
    with TestClient(app) as c:
        async def seed():
            for name in ('alice','bob'):
                who=users[name];m=app.state.matrix
                with connect(s.db) as db:encrypted=db.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()[0]
                password=app.state.cipher.decrypt(encrypted.encode()).decode()
                await m.provision(who['mxid'],password,name);auth=await m.login(who['mxid'],password)
                token=secrets.token_urlsafe(48);proof=secrets.token_urlsafe(48)
                with connect(s.db) as db:
                    db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),who['subject'],name,who['sid'],who['mxid'],auth['device_id'],app.state.cipher.encrypt(auth['access_token'].encode()).decode(),time.time()+7200))
                    db.execute('INSERT INTO native_devices VALUES(?,?,?,?,?)',(digest(proof),who['subject'],who['mxid'],auth['device_id'],time.time()))
                accounts[name]={'uid':who['subject'],'user':name,'mxid':who['mxid'],'device':auth['device_id'],'token':token,'proof':proof}
        c.portal.call(seed)
    server_app=create_app(s)
    @server_app.get('/__fixture/accounts/{name}')
    async def fixture_account(request:Request,name:str):
        assert request.client.host in ('127.0.0.1','::1') and name in accounts
        return accounts[name]
    @server_app.get('/__fixture/new-device/{name}')
    async def fixture_new_device(request:Request,name:str):
        assert request.client.host in ('127.0.0.1','::1') and name in accounts
        who=users[name];m=server_app.state.matrix
        with connect(s.db) as db:encrypted=db.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()[0]
        a=await m.login(who['mxid'],server_app.state.cipher.decrypt(encrypted.encode()).decode())
        token=secrets.token_urlsafe(48)
        with connect(s.db) as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),who['subject'],name,who['sid'],who['mxid'],a['device_id'],server_app.state.cipher.encrypt(a['access_token'].encode()).decode(),time.time()+3600))
        return {'uid':who['subject'],'user':name,'mxid':who['mxid'],'device':a['device_id'],'token':token}
    @server_app.post('/__fixture/audit')
    async def fixture_audit(request:Request):
        # Advance the sealer's test clock only, after all live message checks.
        # Real source ciphertext, archive formats and hashing are unchanged.
        assert request.client.host in ('127.0.0.1','::1')
        import asyncio,signal,base64
        from datetime import datetime,timezone,date
        from backend import archive,history
        def seed_audit():
            today=datetime.now(timezone.utc).date();low,high=archive.day_bounds(today)
            room_ids=[direct.json()['room_id'],group.json()['room_id']];output={}
            process=synapse[4]
            previous=None;paused=False
            try:
                with archive.connect(s.archive_ingest) as pg:
                    pg.execute('SELECT pg_advisory_xact_lock(17919,1)')
                    previous=dict(pg.execute("SELECT * FROM checkpoints WHERE source='synapse'").fetchone())
                    process.send_signal(signal.SIGSTOP);paused=True
                    for index,rid in enumerate(room_ids):
                        pg.execute('INSERT INTO memberships VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',('$fixture-audit-member-'+str(index),rid,users['alice']['mxid'],-10000-index,low-1,'join'))
                    pg.execute("UPDATE checkpoints SET observed_ms=%s,healthy=true WHERE source='synapse'",(high+1000,))
                    pg.execute('INSERT INTO fences VALUES(%s,%s,%s) ON CONFLICT(day) DO UPDATE SET observed_ms=excluded.observed_ms,sequence=excluded.sequence',(today,previous['sequence'],high+1000))
                for rid in room_ids:
                    archive.seal_day(s,rid,today,'events-manifest',now_ms=high+1000)
                    with archive.connect(s.archive_reader) as pg,connect(s.db) as db:
                        records=pg.execute('SELECT * FROM events WHERE room=%s AND day=%s AND type=ANY(%s) ORDER BY sequence',(rid,today,list(history.TYPES))).fetchall()
                        output[rid]=[dict(event_id=r['event_id'],sequence=str(r['sequence']),received_ms=r['received_ms'],sha256=r['body_sha256'],raw=base64.b64encode(bytes(r['body'])).decode(),withdrawn=bool(db.execute('SELECT 1 FROM history_receipts WHERE room=? AND redacts=?',(rid,r['event_id'])).fetchone())) for r in records]
            finally:
                if previous:
                    with archive.connect(s.archive_ingest) as pg:pg.execute("UPDATE checkpoints SET observed_ms=%s,healthy=%s WHERE source='synapse'",(previous['observed_ms'],previous['healthy']))
                if paused:process.send_signal(signal.SIGCONT)
            return {'day':str(today),'rooms':output}
        return await asyncio.to_thread(seed_audit)
    server=uvicorn.Server(uvicorn.Config(server_app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    directory=Path(os.environ.get('YICHAT_ANDROID_FIXTURE_DIRECTORY','/workspace/android-fixture-private'))
    directory.mkdir(mode=0o700,exist_ok=True)
    ready=directory/'ready.json';done=directory/'done'
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        with httpx.Client(trust_env=False,base_url=s.origin,headers={'Authorization':'Bearer '+accounts['alice']['token']},timeout=45) as client:
            direct=client.post('/api/rooms',json={'kind':'direct','users':['bob'],'key':'android-instrument-direct'});direct.raise_for_status()
            group=client.post('/api/rooms',json={'kind':'group','users':['bob'],'name':'Android isolated group','key':'android-instrument-group'});group.raise_for_status()
        with httpx.Client(trust_env=False,base_url=s.origin,headers={'Authorization':'Bearer '+accounts['bob']['token']},timeout=45) as client:
            from urllib.parse import quote
            for rid in (group.json()['room_id'],):
                r=client.post('/_matrix/client/v3/rooms/'+quote(rid,safe='')+'/join',json={});r.raise_for_status()
        ready.write_text(json.dumps({'port':port,'direct':direct.json()['room_id'],'group':group.json()['room_id']}));ready.chmod(0o600)
        deadline=time.monotonic()+min(7200,max(60,int(os.environ.get('YICHAT_ANDROID_FIXTURE_TIMEOUT','3600'))))
        while time.monotonic()<deadline and not done.exists():time.sleep(.5)
        assert done.exists(),'Android fixture timed out; no success asserted'
    finally:
        server.should_exit=True;thread.join(timeout=20)
        idp_server.shutdown();idp_thread.join(timeout=5)
