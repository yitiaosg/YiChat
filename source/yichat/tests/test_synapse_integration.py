"""Real unmodified Synapse, real module, real PostgreSQL. Temporary users/wallets.
No production ports, accounts or assets are used.
"""
import asyncio
import os
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from backend import policy,archive
from backend.app import create_app
from backend.db import connect
from backend.security import digest
import account_social as social


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


@pytest.fixture
def synapse(env,tmp_path):
    s,f,u=env
    port=free_port();s.synapse='http://127.0.0.1:'+str(port)
    root=tmp_path/'synapse';root.mkdir();secret=secrets.token_urlsafe(32)
    config=yaml.safe_load((Path(__file__).parents[1]/'deploy/synapse.yaml.example').read_text())
    config['listeners'][0]['port']=port;config['database']['args']['database']=str(root/'homeserver.db')
    config['signing_key_path']=str(root/'signing.key');config['media_store_path']=str(root/'media');config.pop('log_config',None)
    config['registration_shared_secret']=secret;config.pop('modules',None)
    path=root/'homeserver.yaml';logpath=root/'server.log'
    processes=[]
    def start():
        path.write_text(yaml.safe_dump(config));path.chmod(0o600)
        stream=logpath.open('a')
        process=subprocess.Popen([sys.executable,'-m','synapse.app.homeserver','-c',str(path)],cwd=root,
            env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1])),stdout=stream,stderr=stream)
        processes.append((process,stream))
        for _ in range(100):
            try:
                r=httpx.get(s.synapse+'/_matrix/client/versions',trust_env=False,timeout=1)
                if r.status_code==200:
                    if not config.get('modules'):return process
                    fence=httpx.get(s.synapse+'/_synapse/client/yichat/fence',headers={'Authorization':'Bearer '+s.admin_token},trust_env=False,timeout=1)
                    if fence.status_code==200:return process
            except httpx.HTTPError:pass
            if process.poll() is not None:raise RuntimeError('Isolated Synapse failed; inspect '+str(logpath))
            time.sleep(.1)
        raise RuntimeError('Isolated Synapse timed out')
    def stop(process):process.terminate();process.wait(timeout=15)
    try:
        process=start()
        h=httpx.Client(trust_env=False,timeout=10)
        nonce=h.get(s.synapse+'/_synapse/admin/v1/register').json()['nonce'];password=secrets.token_urlsafe(32);user='test_admin'
        mac=hmac.new(secret.encode(),b'\0'.join([nonce.encode(),user.encode(),password.encode(),b'admin']),hashlib.sha1).hexdigest()
        r=h.post(s.synapse+'/_synapse/admin/v1/register',json={'nonce':nonce,'username':user,'password':password,'admin':True,'mac':mac});r.raise_for_status()
        s.admin_token=r.json()['access_token']
        stop(process)
        if os.environ.get('YICHAT_NOTIFICATION_PAGING_TEST')=='1':config['rc_room_creation']={'per_second':50,'burst_count':100}
        config['modules']=[{'module':'backend.synapse_module.YiChatModule','config':{
            'root':str(s.root),'site':str(s.site),'synapse':s.synapse,'session_key':s.session_key,'admin_token':s.admin_token,
            'archive_ingest':s.archive_ingest,'archive_reader':s.archive_reader}}]
        process=start()
        yield s,f,u,h,process,logpath,start,stop
    finally:
        for process,stream in processes:
            if process.poll() is None:process.terminate();process.wait(timeout=15)
            stream.close()


def test_native_matrix_enforces_policy_and_durable_recovery(synapse):
    s,f,u,h,process,logpath,start,stop=synapse
    app=create_app(s)
    with TestClient(app) as c:
        versions=c.get('/_matrix/client/versions');assert versions.status_code==200 and versions.json()['versions']
        assert c.get('/.well-known/matrix/client').json()['m.homeserver']['base_url']==s.origin
        async def seed():
            tokens={}
            for name in ('alice','bob'):
                who=u[name];mx=app.state.matrix
                with connect(s.db) as db:encrypted=db.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()[0]
                password=app.state.cipher.decrypt(encrypted.encode()).decode()
                await mx.provision(who['mxid'],password,name);auth=await mx.login(who['mxid'],password)
                token=secrets.token_urlsafe(32)
                with connect(s.db) as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),who['subject'],name,who['sid'],who['mxid'],auth['device_id'],app.state.cipher.encrypt(auth['access_token'].encode()).decode(),time.time()+3600))
                tokens[name]={'gateway':token,'raw':auth['access_token'],'device':auth['device_id']}
            return tokens
        tokens=c.portal.call(seed);c.headers.update({'Origin':s.origin,'Authorization':'Bearer '+tokens['alice']['gateway']})
        raw={'Authorization':'Bearer '+tokens['alice']['raw']}
        blocked=h.post(s.synapse+'/_matrix/client/v3/createRoom',headers=raw,json={'is_direct':False});assert blocked.status_code==403,blocked.text
        result=c.post('/api/rooms',json={'kind':'direct','users':['bob']});assert result.status_code==200,result.text
        direct=result.json()['room_id'];room_path='/_matrix/client/v3/rooms/'+quote(direct,safe='')
        joined=c.post('/api/rooms/'+quote(direct,safe='')+'/prepare',json={});assert joined.status_code==200,joined.text
        uploaded=c.post('/api/files/'+quote(direct,safe=''),content=b'OPAQUE_ATTACHMENT_BYTES');assert uploaded.status_code==200,uploaded.text
        fid=uploaded.json()['id']
        body={archive.ATTACHMENT_KEY:[fid],'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'OPAQUE_CIPHERTEXT','session_id':'test','sender_key':'test','device_id':tokens['alice']['device']}
        accepted=h.put(s.synapse+room_path+'/send/m.room.encrypted/one',headers=raw,json=body);assert accepted.status_code==200,accepted.text
        eid=accepted.json()['event_id']
        clear=h.put(s.synapse+room_path+'/send/m.room.message/clear',headers=raw,json={'msgtype':'m.text','body':'no downgrade'});assert clear.status_code==403,clear.text
        # Privacy policy still applies with a valid raw Matrix access token.
        social.mutate(s.accounts,u['bob']['subject'],u['alice']['subject'],'block')
        for typ,data in [('m.room.encrypted',body),('m.reaction',{'m.relates_to':{'rel_type':'m.annotation','event_id':eid,'key':'x'}})]:
            r=h.put(s.synapse+room_path+'/send/'+typ+'/blocked',headers=raw,json=data)
            assert r.status_code==403 and r.json()['code']=='YICHAT_MESSAGE_FORBIDDEN',r.text
        group=c.post('/api/rooms',json={'kind':'group','users':['bob'],'name':'test'});assert group.status_code==200,group.text
        with connect(s.ledger) as db:
            member=db.execute('SELECT membership,pending_event FROM yichat_memberships WHERE room=? AND mxid=?',(direct,u['bob']['mxid'])).fetchone()
            assert member['membership']=='join' and member['pending_event'] is None, 'A deduplicated profile update must not strand a financial barrier'
        gid=group.json()['room_id'];gp='/_matrix/client/v3/rooms/'+quote(gid,safe='')
        encryption=h.get(s.synapse+gp+'/state/m.room.encryption',headers=raw);assert encryption.status_code==404
        text=h.put(s.synapse+gp+'/send/m.room.message/plain',headers=raw,json={'msgtype':'m.text','body':'😀'*2000});assert text.status_code==200,text.text
        too_long=h.put(s.synapse+gp+'/send/m.room.message/large',headers=raw,json={'msgtype':'m.text','body':'😀'*2001});assert too_long.status_code==400,too_long.text
        enc=h.put(s.synapse+gp+'/state/m.room.encryption',headers=raw,json={'algorithm':'m.megolm.v1.aes-sha2'});assert enc.status_code==403,enc.text
        media=h.post(s.synapse+'/_matrix/media/v3/upload',headers=raw,content=b'cannot bypass');assert media.status_code in (404,400),media.text
        # Real native redaction is limited even when bypassing the web gateway.
        normal=h.put(s.synapse+gp+'/send/m.room.message/redaction-normal',headers=raw,json={'msgtype':'m.text','body':'withdraw within five minutes'})
        assert normal.status_code==200,normal.text
        withdrawn=h.put(s.synapse+gp+'/redact/'+quote(normal.json()['event_id'],safe='')+'/redaction-normal-action',headers=raw,json={})
        assert withdrawn.status_code==200,withdrawn.text
        order=c.post('/api/orders',json={'room':gid,'amount':'0.01','count':1,'title':'immutable order','key':'native-order-redaction-test'})
        assert order.status_code==200,order.text
        with connect(s.ledger) as db:db.execute("UPDATE yidt_intents SET state='approved' WHERE id=(SELECT intent FROM yichat_orders WHERE id=?)",(order.json()['id'],))
        funded=c.post('/api/orders/'+order.json()['id']+'/fund',json={});assert funded.status_code==200,funded.text
        business=h.put(s.synapse+gp+'/send/m.room.message/business',headers=raw,json={'msgtype':'m.text','body':'https://chat.yitiao.org/orders/'+order.json()['id'],'org.yitiao.business':{'kind':'order','id':order.json()['id']}})
        assert business.status_code==200,business.text
        refused=h.put(s.synapse+gp+'/redact/'+quote(business.json()['event_id'],safe='')+'/redact-business-action',headers=raw,json={})
        assert refused.status_code==403 and refused.json()['code']=='YICHAT_BUSINESS_IMMUTABLE',refused.text
        forged=h.put(s.synapse+gp+'/send/m.room.message/forged-business',headers=raw,json={'msgtype':'m.text','body':'forged','org.yitiao.business':{'kind':'gift','id':'0'*32}})
        assert forged.status_code==403,forged.text
        # Recovery poll reads the accepted source after a clean process restart.
        stop(process);process=start()
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            with archive.connect(s.archive_reader) as pg:stored=pg.execute('SELECT * FROM events WHERE event_id=%s',(eid,)).fetchone()
            if stored:break
            time.sleep(.1)
        assert stored and b'OPAQUE_CIPHERTEXT' in bytes(stored['body'])
        with archive.connect(s.archive_reader) as pg:
            assert pg.execute('SELECT event_id FROM attachment_references WHERE file=%s',(fid,)).fetchone()['event_id']==eid
        assert b'no downgrade' not in bytes(stored['body'])
        # Real group membership barrier and isolated asset ledger integration.
        rb={'Authorization':'Bearer '+tokens['bob']['raw']}
        assert h.post(s.synapse+gp+'/join',headers=rb,json={}).status_code==200
        o=c.post('/api/orders',json={'room':gid,'key':'actual-synapse-packet-key','amount':'0.01','count':1})
        assert o.status_code==200,o.text
        oid=o.json()['id']
        with connect(s.ledger) as db:db.execute("UPDATE yidt_intents SET state='approved' WHERE id=(SELECT intent FROM yichat_orders WHERE id=?)",(oid,))
        deposit=c.post('/api/orders/'+oid+'/fund',json={});assert deposit.status_code==200,deposit.text
        c.headers['Authorization']='Bearer '+tokens['bob']['gateway']
        claim=c.post('/api/orders/'+oid+'/claim',json={'room':gid});assert claim.status_code==200,claim.text
        assert claim.json()['my_amount']=='0.01'
        c.headers['Authorization']='Bearer '+tokens['alice']['gateway']
        # The actual pinned Synapse purge API is exercised only in this isolated
        # room. Thirty-day eligibility/evidence gates are covered separately.
        boundary=h.put(s.synapse+gp+'/send/m.room.message/boundary',headers=raw,json={'msgtype':'m.text','body':'keep this forward event'});assert boundary.status_code==200,boundary.text
        from backend.archive_worker import submit_purge
        async def submit():
            async with httpx.AsyncClient(headers={'Authorization':'Bearer '+s.admin_token},trust_env=False) as http:
                return await submit_purge(s,http,gid,boundary.json()['event_id'])
        task=c.portal.call(submit)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            status=h.get(s.synapse+'/_synapse/admin/v1/purge_history_status/'+task,headers={'Authorization':'Bearer '+s.admin_token})
            if status.json().get('status')!='active':break
            time.sleep(.1)
        assert status.json()['status']=='complete',status.text
        # Main logout revokes raw tokens while preserving the device and its keys.
        with connect(s.ledger) as db:db.execute("UPDATE yidt_sessions SET revoked=1 WHERE user='alice'")
        assert c.get('/api/session').status_code==401
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            r=h.get(s.synapse+'/_matrix/client/v3/account/whoami',headers=raw)
            if r.status_code==401:break
            time.sleep(.1)
        assert r.status_code==401,r.text
        device=h.get(s.synapse+'/_synapse/admin/v2/users/'+quote(u['alice']['mxid'],safe='')+'/devices',headers={'Authorization':'Bearer '+s.admin_token})
        assert device.status_code==200,device.text
        assert tokens['alice']['device'] in [d['device_id'] for d in device.json()['devices']]
        # Logs may contain Matrix IDs but never app credentials or passwords.
        text=logpath.read_text()
        assert s.session_key not in text and tokens['alice']['raw'] not in text
