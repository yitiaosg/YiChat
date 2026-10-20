import json,time
from unittest.mock import AsyncMock
from test_gateway import gateway
from backend import previews
from backend.db import connect


def test_session_uid_and_authoritative_room_list(gateway):
    c,app,s=gateway
    current=c.get('/api/session').json()
    assert len(current['uid'])==32 and current['uid']!=current['user_id']
    app.state.matrix.call=AsyncMock(return_value={'joined_rooms':['!direct','!group']})
    rows=c.get('/api/rooms').json()
    assert {(r['kind'],r['encryption']) for r in rows}=={('direct','direct'),('group','plain')}


def test_game_preview_never_reveals_active_crash_height(gateway):
    c,app,s=gateway
    with connect(s.ledger) as db:
        db.execute('CREATE TABLE parachute_rounds(share_token TEXT,status TEXT,exit_height REAL,crash_height REAL,started_at REAL)')
        db.execute("INSERT INTO parachute_rounds VALUES(?,'active',NULL,99999,0)",('a'*32,))
        db.execute("INSERT INTO parachute_rounds VALUES(?,'jumped',50,100,0)",('b'*32,))
    url='https://yitiao.org/games/parachute/share/'
    assert previews.metadata(s,url+'a'*32+'/') is None
    result=previews.metadata(s,url+'b'*32+'/')
    assert result['data']['snapshot'] is True and result['data']['exit_height']==50
    assert result['data']['render_version']=='parachute-scene-v1'


def test_public_ycard_reuses_authoritative_design_and_transfer_revokes_preview(gateway):
    c,app,s=gateway
    import ycards
    store=ycards.Store(connect=lambda:connect(s.ledger),cards=s.root/'cards')
    iid='a'*32
    design=ycards.validate_design(dict(name='真实模板',material='plastic',orientation='portrait',corners='round',color='color',layers=[]))
    with connect(s.ledger) as db:
        db.execute('INSERT INTO yc_templates(tid,owner,name,design,cost,created) VALUES(?,?,?,?,?,?)',('TEST','alice','真实模板',json.dumps(design),'{}',0))
        db.execute("INSERT INTO yc_instances VALUES(?, 'TEST',0,'alice',1,1,'cid',?,'alice',0,0)",(iid,json.dumps({'bounds':[0,0,53.98,85.6]})))
        db.execute('INSERT INTO yc_public_cards VALUES(?,?,?)',(iid,'alice',time.time()))
    url='https://yitiao.org/ycards/card/'+iid+'/cid/'
    preview=c.post('/api/preview',json={'url':url,'room':'!direct'})
    assert preview.status_code==200,preview.text
    result=preview.json()['data'];assert result['design']['name']=='真实模板' and 'cost' not in result['design']
    assert result['geometry']['bounds']==[0,0,53.98,85.6] and result['defects']=={'layers':[],'cuts':[],'destroyed':False}
    mine=c.get('/api/cards/mine').json();assert mine['items'][0]['giftable']
    assert c.get('/api/cards/'+iid+'/render').json()['owned']
    with connect(s.ledger) as db:db.execute("UPDATE yc_instances SET owner='bob' WHERE id=?",(iid,))
    assert previews.metadata(s,url) is None
    assert c.get('/api/cards/'+iid+'/render').status_code==404


def test_static_assets_cache_and_crypto_csp(gateway):
    c,app,s=gateway
    page=c.get('/');assert page.status_code==200
    assert "'wasm-unsafe-eval'" in page.headers['content-security-policy']
    assert "frame-ancestors 'none'" in page.headers['content-security-policy']
    assert page.headers['cache-control']=='no-store'
    asset=c.get('/render/ycards-render.js');assert asset.status_code==200 and asset.headers['cache-control']=='public, max-age=3600'
    assert c.get('/render/not-found.js').status_code==404


def test_legacy_order_return_enters_chat_without_a_write(gateway):
    c,app,s=gateway
    response=c.get('/orders/'+'a'*32,follow_redirects=False)
    assert response.status_code==303
    assert response.headers['location']=='/?order='+'a'*32
    assert response.headers['cache-control']=='no-store'
    assert c.get('/orders/not-an-order',follow_redirects=False).status_code==404


def test_room_creation_optional_key_reconciles_ticket_without_creating_twice(gateway):
    c,app,s=gateway
    from backend import policy
    key='original-room-request-key'
    async def create(method,url,token=None,data=None,params=None):
        if url.endswith('/createRoom'):
            ticket=data['creation_content']['org.yitiao.ticket']
            policy.register_room(s,ticket,'!created')
            return {'room_id':'!created'}
        return {}
    app.state.matrix.call=AsyncMock(side_effect=create)
    app.state.matrix.provision=AsyncMock()
    payload={'kind':'direct','users':['bob'],'key':key}
    assert c.post('/api/rooms',json=payload).json()['room_id']=='!created'
    assert c.post('/api/rooms',json=payload).json()['room_id']=='!created'
    assert sum(1 for call in app.state.matrix.call.call_args_list if call.args[1].endswith('/createRoom'))==1
    result=c.get('/api/room-creations/'+key).json()
    assert result=={'state':'created','room_id':'!created','retry_after_ms':result['retry_after_ms']}
    assert c.post('/api/rooms',json=dict(payload,users=['carol'])).status_code==409
    c.cookies.clear();c.headers['Authorization']='Bearer bob-token'
    assert c.get('/api/room-creations/'+key).status_code==404


def test_profile_lookup_by_username_includes_users_not_yet_provisioned_in_matrix(gateway):
    c,app,s=gateway
    with connect(s.accounts) as db:
        db.execute("ALTER TABLE users ADD COLUMN biography TEXT DEFAULT ''")
        db.execute("UPDATE users SET biography='主站简介' WHERE username='carol'")
    with connect(s.db) as db:db.execute("DELETE FROM identities WHERE username='carol'")
    response=c.post('/api/profiles',json={'usernames':['carol']})
    assert response.status_code==200,response.text
    row=response.json()[0];assert row['username']=='carol' and row['biography']=='主站简介'
    assert row['profile_url'].endswith('/@carol/') and row['mxid'].startswith('@u_')
    assert c.post('/api/profiles',json={'usernames':['carol']*101}).status_code==400
