import time,secrets,concurrent.futures,multiprocessing
import pytest
from backend.security import reserve_rate,authenticate,digest,upload_slot
from backend.db import connect
from backend.previews import parse
from fastapi import HTTPException

def test_main_session_revoke_all_existing_gateway_tokens(env):
    s,f,u=env
    with connect(s.db) as c:
        for token in ('one','two'):c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),u['alice']['subject'],'alice','sid-alice','@alice:chat.yitiao.org','d','encrypted',time.time()+60))
    assert authenticate(s,'one')['subject']==u['alice']['subject']
    with connect(s.ledger) as c:c.execute("UPDATE yidt_sessions SET revoked=1 WHERE sid='sid-alice'")
    for token in ('one','two'):
        with pytest.raises(HTTPException) as e:authenticate(s,token)
        assert e.value.status_code==401

def test_upload_aggregate_multiple_workers(env):
    s,f,u=env
    def take(_):return reserve_rate(s,'alice','upload',100000,500000,now=100,max_wait=10)
    with concurrent.futures.ProcessPoolExecutor(max_workers=2,mp_context=multiprocessing.get_context('spawn')) as pool:
        # Picklable helper below models independent server processes.
        values=list(pool.map(reserve_process,[(s,i) for i in range(10)]))
    assert sorted(round(x,4) for x in values)==[round(i*.2,4) for i in range(1,11)]
    assert reserve_rate(s,'bob','upload',100000,500000,now=100)==pytest.approx(.2)

def reserve_process(args):return reserve_rate(args[0],'alice','upload',100000,500000,now=100,max_wait=10)

@pytest.mark.asyncio
async def test_concurrency_daily_limits(env):
    s,f,u=env;s.upload_concurrency=2;s.daily_bytes=100
    await upload_slot(s,'alice','one',30);await upload_slot(s,'alice','two',30)
    with pytest.raises(HTTPException):await upload_slot(s,'alice','three',1)
    with connect(s.db) as c:c.execute('DELETE FROM upload_leases')
    with pytest.raises(HTTPException) as e:await upload_slot(s,'alice','four',41)
    assert e.value.status_code==429 and e.value.detail['errcode']=='M_LIMIT_EXCEEDED'
    assert e.value.detail['retry_after_ms']>0 and int(e.value.headers['Retry-After'])>0

@pytest.mark.parametrize('url',[
 'http://127.0.0.1/','https://yitiao.org.evil.test/ycards/card/a/b/',
 'https://yitiao.org@127.0.0.1/','https://yitiao.org:443/ycards/card/a/b/',
 'https://yitiao.org/%2e%2e/admin','https://yitiao.org\\@evil.test/',
 'https://yidt.yitiao.org/explorer/tx/'+'a'*64+'?redirect=http://127.0.0.1',
 'javascript:alert(1)','https://yitiao.org/games/parachute/share/abc/',
])
def test_ssrf_route_allowlist(url):assert parse(url) is None

def test_known_routes():
 assert parse('https://yidt.yitiao.org/explorer/tx/'+'a'*64)[0]=='transaction'
 assert parse('https://yitiao.org/ycards/card/'+'a'*32+'/CID-1/')[0]=='card'
 assert parse('https://yitiao.org/games/parachute/share/'+'a'*32+'/')[0]=='game'
 assert parse('https://chat.yitiao.org/orders/'+'a'*32)[0]=='order'


def request_process(args):
    from backend.security import request_limit
    try:request_limit(args[0],'user:shared-uid',now=100);return True
    except HTTPException as e:assert e.status_code==429;return False


def test_exact_rolling_window_shared_by_independent_processes(env):
    from backend.security import request_limit
    s,f,u=env
    with concurrent.futures.ProcessPoolExecutor(max_workers=4,mp_context=multiprocessing.get_context('spawn')) as pool:
        accepted=list(pool.map(request_process,[(s,i) for i in range(30)]))
    assert sum(accepted)==10
    with pytest.raises(HTTPException):request_limit(s,'user:shared-uid',now=100.999)
    request_limit(s,'user:shared-uid',now=101)


@pytest.mark.asyncio
@pytest.mark.parametrize('path',['/api/files/!room','/_matrix/media/v3/upload'])
async def test_actual_hundred_million_byte_limit_without_declared_length(env,path):
    from backend.limits import BodyLimitMiddleware
    s,f,u=env;actual=0
    async def consume(scope,receive,send):
        while True:
            part=await receive()
            if not part.get('more_body'):return
    remaining=100000001
    async def receive():
        nonlocal remaining,actual
        size=min(65536,remaining);remaining-=size;actual+=size
        return {'type':'http.request','body':b'x'*size,'more_body':bool(remaining)}
    middleware=BodyLimitMiddleware(consume,s)
    with pytest.raises(HTTPException) as e:await middleware({'type':'http','method':'POST','path':path},receive,None)
    assert e.value.status_code==413 and actual==100000001
