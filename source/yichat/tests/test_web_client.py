"""Real Chromium + Rust WASM + isolated Synapse/module/ledger/PG, no live assets."""
import os,json,time,threading,secrets,subprocess
from pathlib import Path
import uvicorn
from fastapi.testclient import TestClient
from test_synapse_integration import synapse,free_port
from backend.app import create_app
from backend.db import connect
from backend.security import digest


def test_web_client_real_e2ee_and_plain_group(synapse,monkeypatch):
    s,f,users,h,process,logpath,start,stop=synapse
    s.origin='http://127.0.0.1:'+str(free_port())
    # Reuse real card authoring/production definitions with isolated fixture data.
    import ycards
    store=ycards.Store(connect=lambda:connect(s.ledger),cards=s.root/'cards')
    design=ycards.validate_design({'name':'极光纪念卡','material':'plastic','orientation':'portrait','corners':'round','color':'color','layers':[{'id':'foil1','type':'foil','effect':'color'},{'id':'glow1','type':'glow','effect':'ring','tint':'#92dfbb'}]})
    design.update(width_mm=63,height_mm=88)
    iid='a'*32;token='G'*32
    with connect(s.ledger) as db:
        db.execute('INSERT INTO yc_templates(tid,owner,name,design,cost,created) VALUES(?,?,?,?,?,?)',('WEBTEST','alice','极光纪念卡',json.dumps(design),'{}',0))
        db.execute("INSERT INTO yc_instances VALUES(?, 'WEBTEST',0,'alice',1,1,'WEB-CID',?,'alice',0,0)",(iid,json.dumps({'bounds':[0,0,63,88],'cells':[1],'corners':[3.18]*4})))
        db.execute('INSERT INTO yc_public_cards VALUES(?,?,?)',(iid,'alice',time.time()))
        db.execute('CREATE TABLE parachute_rounds(share_token TEXT PRIMARY KEY,status TEXT,exit_height REAL,crash_height REAL,started_at REAL)')
        db.execute("INSERT INTO parachute_rounds VALUES(?,'jumped',52.38,89.12,?)",(token,time.time()))
    # The existing OIDC provider runs locally with a fixture login UI and RSA key.
    # No HTTP mock transport: browser follows authorize -> login -> callback -> token/JWKS.
    from flask import Flask,request as flask_request,session as flask_session,redirect as flask_redirect
    from urllib.parse import quote as urlquote
    from werkzeug.serving import make_server
    import sys
    sys.path.append('/opt/vps-python/lib/python3.11/site-packages')
    from PIL import Image
    avatar_dir=s.site/'static/avatar';avatar_dir.mkdir(parents=True)
    for name,color in [('alice','#207c66'),('bob','#477aa5')]:Image.new('RGB',(40,40),color).save(avatar_dir/(name+'.jpg'))
    with connect(s.accounts) as db:
        db.execute("ALTER TABLE users ADD COLUMN biography TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE users SET biography='Bob 的主站自我介绍' WHERE username='bob'")
    import oidc_provider
    idp_port=free_port();s.issuer='http://127.0.0.1:'+str(idp_port);s.idp_internal=s.issuer
    idp=Flask('isolated_oidc');idp.secret_key=secrets.token_urlsafe(32)
    oidc_provider.CLIENTS['yichat']={'client_secret':'','redirect_uris':[s.origin+'/auth/callback'],'name':'YiChat'}
    def person(name):
        with connect(s.accounts) as db:
            row=db.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
            return dict(row) if row else None
    oidc_provider.init_oidc(idp,oidc_provider.OIDCDeps(issuer=s.issuer,db_conn=lambda:connect(s.accounts),get_user=person,
        current_username=lambda:flask_session.get('user'),claims_for=lambda user:{'sub':user['username'],'user_id':users[user['username']]['subject'],'preferred_username':user['username']},
        login_redirect=lambda next:flask_redirect('/login?next='+urlquote(next,safe='')),deny_page=lambda title,message:(message,403),key_path=s.root/'fixture-oidc.pem'))
    @idp.route('/login',methods=['GET','POST'])
    def fixture_login():
        if flask_request.method=='POST':
            if flask_request.form.get('username')!='alice' or flask_request.form.get('password')!='IsolatedPassword123!':return 'Invalid fixture credentials',403
            sid=secrets.token_urlsafe(32)
            with connect(s.ledger) as db:db.execute('INSERT INTO yidt_sessions VALUES(?,?,?,0)',(sid,'alice',time.time()+3600))
            flask_session.update(user='alice',yidt_sid=sid)
            return flask_redirect(flask_request.args.get('next','/'))
        return '<form method="post"><label>用户名<input name="username"></label><label>密码<input type="password" name="password"></label><button>登录</button></form>'
    idp_server=make_server('127.0.0.1',idp_port,idp,threaded=True);idp_thread=threading.Thread(target=idp_server.serve_forever,daemon=True);idp_thread.start()

    # Original YIDT authorization page and POST, on a loopback-only fixture.
    import yidt
    wallet_app=yidt.app
    import yidt_bridge
    monkeypatch.setitem(yidt_bridge.RETURN,'yichat',s.origin+'/')
    from werkzeug.security import generate_password_hash
    with connect(s.ledger) as db:db.execute('INSERT INTO yidt_pins(user,hash) VALUES(?,?)',('alice',generate_password_hash('IsolatedPayment234!')))
    # The production CSP allows the exact HTTPS chat origin; this local fixture
    # additionally allows its loopback origin so redirects can be tested without TLS.
    def fixture_wallet_security(response):
        response=yidt.secure(response)
        response.headers['Content-Security-Policy']=response.headers['Content-Security-Policy'].replace("form-action 'self' ","form-action 'self' "+s.origin+' ')
        return response
    wallet_app.after_request_funcs[None]=[fixture_wallet_security if fn is yidt.secure else fn for fn in wallet_app.after_request_funcs[None]]
    @wallet_app.get('/login-fixture/<username>')
    def wallet_fixture_login(username):
        if username not in users:return '',403
        flask_session.update(user=username,sid='sid-'+username,csrf=secrets.token_urlsafe(32))
        return 'Isolated wallet session ready'
    wallet_server=make_server('127.0.0.1',free_port(),wallet_app,threaded=True)
    wallet_origin='http://127.0.0.1:'+str(wallet_server.server_port)
    wallet_thread=threading.Thread(target=wallet_server.serve_forever,daemon=True);wallet_thread.start()

    app=create_app(s)
    with TestClient(app) as c:
        async def seed():
            tokens={}
            for label,name in [('alice','alice'),('alice2','alice'),('bob','bob'),('carol','carol')]:
                who=users[name];mx=app.state.matrix
                with connect(s.db) as db:encrypted=db.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()[0]
                password=app.state.cipher.decrypt(encrypted.encode()).decode()
                await mx.provision(who['mxid'],password,name);auth=await mx.login(who['mxid'],password)
                gateway=secrets.token_urlsafe(32)
                with connect(s.db) as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(gateway),who['subject'],name,who['sid'],who['mxid'],auth['device_id'],app.state.cipher.encrypt(auth['access_token'].encode()).decode(),time.time()+3600))
                tokens[label]=gateway
            return tokens
        tokens=c.portal.call(seed)
    server_app=create_app(s)
    from fastapi import Request
    @server_app.post('/__test/control')
    async def control(request:Request):
        data=await request.json()
        if data['action'] in ('block','unblock'):
            import account_social
            account_social.mutate(s.accounts,users['bob']['subject'],users['alice']['subject'],data['action'])
        elif data['action']=='age_history_packet':
            # Fault injection only in the loopback fixture: retained body is
            # removed and established cleanup receipts stand in for 32 days.
            from backend import archive
            eid=data['event_id'];old=int(time.time()*1000)-32*86400000
            with connect(s.db) as db:
                row=db.execute('SELECT * FROM history_receipts WHERE event_id=?',(eid,)).fetchone()
                if not row:return {'error':'Packet must be cached first'}
                db.execute('UPDATE history_receipts SET received_ms=? WHERE event_id=?',(old,eid))
            with archive.connect(s.archive_ingest) as pg:
                for offset,name in enumerate(('alice','bob')):
                    pg.execute('INSERT INTO memberships VALUES(%s,%s,%s,%s,%s,%s)',('$peer-fixture-'+name,row['room'],users[name]['mxid'],-10000+offset,old-1000,'join'))
            day=archive.utcday(old)
            with archive.connect(s.archive_sealer) as pg:
                for spec in archive.SPECS:
                    pg.execute("INSERT INTO archives(room,day,version,spec,sha256,bytes,event_count,first_sequence,last_sequence,attachment_sha256,state,permission_spec,fence_sequence) VALUES(%s,%s,1,%s,%s,1,1,%s,%s,%s,'sealed',%s,%s)",(row['room'],day,spec,'a'*64,row['sequence'],row['sequence'],'b'*64,archive.PERMISSION_SPEC,row['sequence']))
                    pg.execute('INSERT INTO archive_audit(room,day,version,spec) VALUES(%s,%s,1,%s)',(row['room'],day,spec))
            with archive.connect(s.archive_cleanup) as pg:
                pg.execute('DELETE FROM events WHERE event_id=%s',(eid,))
                pg.execute('UPDATE archive_audit SET cleaned_at=now(),history_done=true WHERE room=%s AND day=%s',(row['room'],day))
        elif data['action']=='unbind_wallet':
            with connect(s.ledger) as db:db.execute("DELETE FROM yidt_bindings WHERE user='alice' AND service='yichat'")
        elif data['action']=='approve':
            with connect(s.ledger) as db:db.execute("UPDATE yidt_intents SET state='approved' WHERE id=(SELECT intent FROM yichat_orders WHERE id=?)",(data['id'],))
        return {'ok':True}
    @server_app.get('/__test/info')
    async def info():
        with connect(s.ledger) as db:
            return {'orders':[dict(r) for r in db.execute('SELECT id,deposit,state FROM yichat_orders')],
                    'claims':db.execute("SELECT count(*) FROM yichat_payouts WHERE kind='claim'").fetchone()[0],
                    'gifts':db.execute('SELECT count(*) FROM yichat_card_gifts').fetchone()[0]}
    server=uvicorn.Server(uvicorn.Config(server_app,host='127.0.0.1',port=int(s.origin.rsplit(':',1)[1]),access_log=False,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.05)
        script=Path(__file__).parent/'browser'
        child=subprocess.Popen(['node','chat.mjs'],cwd=script,env=dict(os.environ,CHAT_TEST_ORIGIN=s.origin,CHAT_TEST_TOKENS=json.dumps(tokens),CHAT_TEST_WALLET=wallet_origin),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        output=[]
        try:
            for line in child.stdout:
                output.append(line)
                if line.startswith(('CHECK','HTTP','PAGE','{')):print(line,end='',flush=True)
            child.wait(timeout=600)
        finally:
            if child.poll() is None:child.terminate();child.wait(timeout=15)
        (script/'artifacts').mkdir(exist_ok=True)
        (script/'artifacts'/'last-run.log').write_text(''.join(output))
        assert child.returncode==0,''.join(output)
    finally:
        server.should_exit=True;thread.join(timeout=15);idp_server.shutdown();idp_thread.join(timeout=5);wallet_server.shutdown();wallet_thread.join(timeout=5)
