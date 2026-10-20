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
            for rid in (direct.json()['room_id'],group.json()['room_id']):
                r=client.post('/_matrix/client/v3/rooms/'+quote(rid,safe='')+'/join',json={});r.raise_for_status()
        ready.write_text(json.dumps({'port':port,'direct':direct.json()['room_id'],'group':group.json()['room_id']}));ready.chmod(0o600)
        deadline=time.monotonic()+3600
        while time.monotonic()<deadline and not done.exists():time.sleep(.5)
        assert done.exists(),'Android fixture timed out; no success asserted'
    finally:
        server.should_exit=True;thread.join(timeout=20)
