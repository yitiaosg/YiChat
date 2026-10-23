"""Focused persisted pair reservation checks. Temporary SQLite only."""
import concurrent.futures,time
from fastapi import HTTPException
from backend.config import Settings
from backend.db import migrate,connect
from backend import direct,policy

def test_pair_reservation_is_shared_across_reversed_users_and_concurrent_keys(tmp_path):
    s=Settings(root=tmp_path/'chat',site=tmp_path/'site');migrate(s)
    def reserve(i):
        actor,peer=('@alice:test','@bob:test') if i%2==0 else ('@bob:test','@alice:test')
        try:return direct.reserve_creation(s,{'mxid':actor,'subject':actor},'direct',[peer],'pair-reservation-'+str(i),'payload')
        except HTTPException as e:return e.status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(reserve,range(8)))
    assert len([r for r in results if isinstance(r,tuple)])==1
    assert [r for r in results if isinstance(r,int)]==[409]*7
    with connect(s.db) as c:
        assert c.execute('SELECT count(*) FROM direct_pairs').fetchone()[0]==1
        token=c.execute('SELECT ticket FROM direct_pairs').fetchone()[0]
    policy.register_room(s,token,'!one:test')
    assert direct.reserve_creation(s,{'mxid':'@bob:test','subject':'bob'},'direct',['@alice:test'],'pair-retry-new-key','payload')==(None,'!one:test')
    with connect(s.db) as c:assert c.execute('SELECT count(*) FROM rooms').fetchone()[0]==1

def test_pending_ticket_expiry_does_not_allow_old_ticket_to_claim_second_room(tmp_path):
    s=Settings(root=tmp_path/'chat',site=tmp_path/'site');migrate(s);who={'mxid':'@alice:test','subject':'alice'}
    first,_=direct.reserve_creation(s,who,'direct',['@bob:test'],'pair-original-key','payload')
    with connect(s.db) as c:c.execute('UPDATE room_tickets SET expires=? WHERE token=?',(time.time()-1,first))
    second,_=direct.reserve_creation(s,who,'direct',['@bob:test'],'pair-replacement-key','payload')
    assert first!=second
    try:policy.register_room(s,first,'!stale:test')
    except HTTPException as e:assert e.status_code in (403,409)
    else:raise AssertionError('Stale ticket must not register another private room')
    policy.register_room(s,second,'!one:test')
