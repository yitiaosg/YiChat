import concurrent.futures
import secrets
import pytest
from fastapi import HTTPException
from backend import assets
from backend.db import connect
from test_gateway import gateway

@pytest.fixture
def cards(env):
    s,f,u=env
    # The actual YCards schema and transfer triggers are used, with isolated
    # records and the same authoritative ledger connection as the service.
    import ycards
    ycards.Store(connect=lambda:connect(s.ledger),cards=s.root/'cards')
    assets.migrate(s)
    iid='a'*32
    with connect(s.ledger) as c:
        c.execute("INSERT INTO yc_templates(tid,owner,name,design,cost,created) VALUES('gift-fixture','alice','fixture','{}','{}',0)")
        c.execute("INSERT INTO yc_instances VALUES(?, 'gift-fixture',0,'alice',1,1,'fixture-cid','{}','alice',0,0)",(iid,))
    return s,f,u,iid


def test_ycard_gift_atomic_idempotent_owner_and_recipient(cards):
    s,f,u,iid=cards;room={'room':'!test:chat.yitiao.org','kind':'group'}
    key=secrets.token_urlsafe(24);data={'instance':iid,'recipient':'bob','key':key}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:assets.gift(s,u['alice'],room,data),range(4)))
    assert len({r['id'] for r in results})==1
    with connect(s.ledger) as c:assert c.execute('SELECT owner FROM yc_instances WHERE id=?',(iid,)).fetchone()[0]=='bob'
    with pytest.raises(HTTPException):assets.gift(s,u['alice'],room,dict(data,key=secrets.token_urlsafe(24)))
    with pytest.raises(HTTPException):assets.gift(s,u['bob'],room,dict(data,recipient='bob',key=secrets.token_urlsafe(24)))


def test_ycard_on_sale_cannot_be_gifted(cards):
    s,f,u,iid=cards
    with connect(s.ledger) as c:c.execute("INSERT INTO yc_listings VALUES(?,?,'alice',100,'active',0,0)",('b'*32,iid))
    with pytest.raises(HTTPException) as e:assets.gift(s,u['alice'],{'room':'!test:chat.yitiao.org','kind':'group'},{'instance':iid,'recipient':'bob','key':secrets.token_urlsafe(24)})
    assert e.value.status_code==409


def test_gift_unknown_result_can_be_read_by_original_sender(cards,gateway):
    s,f,u,iid=cards;c,app,settings=gateway
    key='gift-response-loss-key'
    result=assets.gift(s,u['alice'],{'room':'!test:chat.yitiao.org','kind':'group'},{'instance':iid,'recipient':'bob','key':key})
    recovered=c.get('/api/cards/gifts/by-key/'+key)
    assert recovered.status_code==200 and recovered.json()['id']==result['id']
    assert recovered.json()['state']=='transferred'
    c.cookies.clear();c.headers['Authorization']='Bearer bob-token'
    assert c.get('/api/cards/gifts/by-key/'+key).status_code==404


def test_gift_url_survives_transfer_and_destroyed_card_has_no_preview(cards,gateway):
    s,f,u,iid=cards;c,app,settings=gateway
    from backend import policy
    with connect(s.ledger) as db:
        for name in ('alice','bob'):db.execute("INSERT OR REPLACE INTO yichat_memberships VALUES('!direct',?,'join','fixture',NULL,NULL,NULL)",(u[name]['mxid'],))
        db.execute('UPDATE yc_templates SET design=? WHERE tid=?',('{"name":"赠送时卡面","cost":{"private":123}}','gift-fixture'))
    result=assets.gift(s,u['alice'],policy.room(s,'!direct'),{'instance':iid,'recipient':'bob','key':'snapshot-idempotency-key'})
    with connect(s.ledger) as db:db.execute("UPDATE yc_instances SET owner='carol' WHERE id=?",(iid,))
    response=c.get('/api/cards/gifts/'+result['id'],params={'room':'!direct'})
    assert response.status_code==200,response.text
    snapshot=response.json();assert snapshot['snapshot'] is False
    assert snapshot['url']==s.origin+'/gifts/'+result['id']
    from backend.history import business_reference
    reference={'body':'https://chat.yitiao.org/gifts/'+result['id']}
    assert business_reference(s,'!direct',u['alice']['mxid'],reference)=='gift:'+result['id']
    assert business_reference(s,'!group',u['alice']['mxid'],reference) is None
    assert c.get('/api/cards/gifts/'+result['id']).json()['card']['id']==iid
    with connect(s.ledger) as db:assert db.execute('SELECT count(*) FROM yichat_gift_renderings').fetchone()[0]==0
    assert snapshot['card']['design']['name']=='赠送时卡面' and 'cost' not in snapshot['card']['design']
    assert c.get('/api/cards/gifts/'+result['id'],params={'room':'!group'}).status_code==404
    with connect(s.ledger) as db:db.execute('INSERT INTO yc_destroyed VALUES(?,?,?)',(iid,'carol',1))
    destroyed=c.get('/api/cards/gifts/'+result['id'],params={'room':'!direct'})
    assert destroyed.status_code==410
    assert destroyed.json()['code']=='YICHAT_GIFT_CARD_DESTROYED'
    assert 'card' not in destroyed.json()
    assert c.get('/gifts/'+result['id'],follow_redirects=False).headers['location']=='/?gift='+result['id']


def test_yichat_no_longer_serves_android_apks(gateway):
    c,app,s=gateway
    directory=s.root/'downloads';directory.mkdir(parents=True)
    (directory/'YiChat-latest.apk').write_bytes(b'isolated fixture apk')
    response=c.get('/app/download',follow_redirects=False)
    assert response.status_code==404
    assert response.content!=b'isolated fixture apk'
