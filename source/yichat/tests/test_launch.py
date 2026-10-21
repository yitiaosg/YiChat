import concurrent.futures,secrets,time
from urllib.parse import quote
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.db import connect
from backend.security import digest
from backend import policy,groups
from test_funds import create,fund
from test_synapse_integration import synapse

def test_packet_stress_never_pays_twice_and_sender_covers_all_gas(env):
    s,f,u=env
    with pytest.raises(HTTPException):create(env,7,'0.07')
    with pytest.raises(HTTPException):create(env,5,'0.0499999999999999')
    with connect(s.ledger) as c:
        source=f.binding(c,'alice')['address'];before=int(f.y.wallet(c,source)['balance'])
    o=fund(env,create(env,5,'0.05'));names=['bob','carol','dave','eve','shared']
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
        results=list(pool.map(lambda n:f.claim(u[n],o['id'],o['room']),[names[i%5] for i in range(1200)]))
    assert len({r['my_amount'] for r in results})==1
    with connect(s.ledger) as c:
        payouts=c.execute("SELECT * FROM yichat_payouts WHERE order_id=?",(o['id'],)).fetchall()
        assert len(payouts)==5 and len({p['wallet'] for p in payouts})==5
        assert sum(int(p['principal']) for p in payouts)==f.y.units('0.05')
        assert before-int(f.y.wallet(c,source)['balance'])==f.y.units('0.05')+6*f.y.GAS
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:list(pool.map(lambda p:f.settle_one(p['id']),payouts*40))
    f.reconcile()
    with connect(s.ledger) as c:
        assert c.execute("SELECT count(*) FROM yidt_transactions WHERE kind='yichat_payout'").fetchone()[0]==5
        assert int(c.execute('SELECT remaining FROM yichat_orders WHERE id=?',(o['id'],)).fetchone()[0])==0
        assert all(int(c.execute('SELECT credit FROM yidt_transactions WHERE hash=?',(p['tx'],)).fetchone()[0])==f.y.units('0.01') for p in c.execute('SELECT * FROM yichat_payouts'))

@pytest.mark.parametrize("version",["11","12"])
def test_real_group_management_auto_direct_and_privacy(synapse,version):
    s,f,u,h,*_=synapse
    with TestClient(create_app(s)) as c:
        async def seed():
            out={}
            for name in ('alice','bob','carol','dave'):
                who=u[name];app=c.app;m=app.state.matrix
                with connect(s.db) as db:encrypted=db.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()[0]
                password=app.state.cipher.decrypt(encrypted.encode()).decode();await m.provision(who['mxid'],password,name);a=await m.login(who['mxid'],password);token=secrets.token_urlsafe(48)
                with connect(s.db) as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),who['subject'],name,who['sid'],who['mxid'],a['device_id'],app.state.cipher.encrypt(a['access_token'].encode()).decode(),time.time()+3600))
                out[name]=token
            return out
        tokens=c.portal.call(seed)
        original=c.app.state.matrix.call
        async def call(method,path,token=None,data=None,params=None):
            if path.endswith('/createRoom') and data and not data.get('is_direct'):data=dict(data,room_version=version)
            return await original(method,path,token,data,params)
        c.app.state.matrix.call=call
        def req(name,method,path,body=None):
            for _ in range(8):
                r=c.request(method,path,headers={'Authorization':'Bearer '+tokens[name],'Origin':s.origin},json=body)
                if r.status_code!=429:return r
                time.sleep(max(0.2,r.json().get('retry_after_ms',1000)/1000))
            return r
        def ok(r):assert r.status_code==200,r.text;return r.json()
        dm=ok(req('alice','POST','/api/rooms',{'kind':'direct','users':['bob'],'key':'launch-direct-create-001'}))['room_id'];dmq=quote(dm,safe='')
        assert not ok(req('bob','GET','/api/rooms'))
        sync=ok(req('bob','GET','/_matrix/client/v3/sync?timeout=0'));assert dm not in sync.get('rooms',{}).get('invite',{})
        policy.social.mutate(s.accounts,u['bob']['subject'],u['alice']['subject'],'block')
        denied=req('alice','POST','/api/rooms/'+dmq+'/prepare',{});assert denied.status_code==403 and denied.json()['code']=='YICHAT_MESSAGE_FORBIDDEN'
        upload=c.post('/api/files/'+dmq,content=b'never-store-blocked-file',headers={'Authorization':'Bearer '+tokens['alice'],'Origin':s.origin});assert upload.status_code==403
        assert not list(s.objects.iterdir())
        assert req('alice','PUT','/_matrix/client/v3/rooms/'+dmq+'/send/m.room.encrypted/blocked',{'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'cipher'}).status_code==403
        policy.social.mutate(s.accounts,u['bob']['subject'],u['alice']['subject'],'unblock')
        ok(req('alice','POST','/api/rooms/'+dmq+'/prepare',{}));assert u['bob']['mxid'] in groups.joined(s,dm)
        assert not ok(req('bob','GET','/api/rooms'))
        ok(req('alice','PUT','/_matrix/client/v3/rooms/'+dmq+'/send/m.room.encrypted/first',{'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'OPAQUE','session_id':'fixture','sender_key':'fixture','device_id':'fixture'}))
        assert any(r['room']==dm for r in ok(req('bob','GET','/api/rooms')))
        assert dm in ok(req('bob','GET','/_matrix/client/v3/sync?timeout=0&since='+sync['next_batch']))['rooms']['join']
        assert ok(req('alice','POST','/api/rooms',{'kind':'direct','users':['bob'],'key':'launch-direct-create-002'}))['room_id']==dm
        group=ok(req('alice','POST','/api/rooms',{'kind':'group','users':['bob','carol'],'name':'Launch controls','key':'launch-group-create-001'}))['room_id'];gq=quote(group,safe='')
        for name in ('bob','carol'):ok(req(name,'POST','/_matrix/client/v3/rooms/'+gq+'/join',{}))
        g=ok(req('alice','GET','/api/groups/'+gq));assert len(g['number'])==9 and g['member_count']==3
        def manage(name,action,**data):return req(name,'POST','/api/groups/'+gq+'/manage',{'action':action,'key':secrets.token_hex(16),**data})
        ok(manage('alice','settings',searchable=True,member_invites=False))
        assert req('bob','POST','/api/rooms/'+gq+'/invite',{'username':'dave'}).status_code==403
        ok(req('dave','POST','/api/group-numbers/'+g['number']+'/join',{'key':'launch-group-number-join-001'}));assert u['dave']['mxid'] in groups.joined(s,group)
        ok(manage('alice','admin',target=u['bob']['mxid'],enabled=True))
        assert manage('bob','mute',target=u['alice']['mxid'],seconds=60).status_code==403
        assert manage('alice','mute',target=u['bob']['mxid'],seconds=86401).status_code==400
        ok(manage('alice','mute',target=u['bob']['mxid'],seconds=30))
        assert req('bob','PUT','/_matrix/client/v3/rooms/'+gq+'/send/m.room.message/muted',{'msgtype':'m.text','body':'no'}).status_code==403
        ok(manage('alice','mute',target=u['bob']['mxid'],seconds=0))
        event=ok(req('carol','PUT','/_matrix/client/v3/rooms/'+gq+'/send/m.room.message/normal',{'msgtype':'m.text','body':'original'}))
        assert req('bob','PUT','/_matrix/client/v3/rooms/'+gq+'/redact/'+quote(event['event_id'],safe='')+'/admin-raw',{}).status_code==403
        ok(manage('alice','redact',target=u['carol']['mxid'],event=event['event_id']))
        ok(manage('bob','kick',target=u['dave']['mxid'],ban=False))
        left_event=ok(req('carol','PUT','/_matrix/client/v3/rooms/'+gq+'/send/m.room.message/before-kick',{'msgtype':'m.text','body':'owner may redact after leave'}))
        ok(manage('alice','kick',target=u['carol']['mxid'],ban=True))
        ok(manage('alice','redact',target=u['carol']['mxid'],event=left_event['event_id']))
        assert req('carol','POST','/api/group-numbers/'+g['number']+'/join',{'key':'launch-group-number-join-002'}).status_code==403
        ok(manage('alice','transfer',target=u['bob']['mxid']))
        assert manage('alice','settings',searchable=False,member_invites=False).status_code==403
        assert req('alice','POST','/_matrix/client/v3/rooms/'+gq+'/kick',{'user_id':u['bob']['mxid']}).status_code==403
        assert req('alice','PUT','/_matrix/client/v3/rooms/'+gq+'/state/m.room.power_levels',{'users':{u['alice']['mxid']:100}}).status_code==403
        ok(manage('bob','settings',searchable=False,member_invites=True))
        assert req('dave','GET','/api/group-numbers/'+g['number']).status_code==404
        ok(manage('bob','kick',target=u['alice']['mxid'],ban=True));assert u['alice']['mxid'] not in groups.joined(s,group)
