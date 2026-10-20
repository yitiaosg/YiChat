import concurrent.futures
import sqlite3
import pytest
from backend import policy
import account_social as social


def test_stable_identity_rename_and_matrix_mapping(env):
    s,f,u=env
    uid=u['alice']['subject'];before=policy.mxid(s,'alice')
    with social.database(s.accounts,True) as c:c.execute("UPDATE users SET username='newname' WHERE username='alice'")
    assert social.get_identity(s.accounts,username='newname')['uid']==uid
    assert policy.mxid(s,'newname')==before
    with social.database(s.accounts) as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute('UPDATE account_ids SET uid=? WHERE uid=?',('f'*32,uid))


def test_relations_idempotent_block_clears_both_and_unblock_does_not_restore(env):
    s,f,u=env;a=u['alice']['subject'];b=u['bob']['subject']
    social.mutate(s.accounts,a,b,'follow');social.mutate(s.accounts,a,b,'follow')
    out=social.mutate(s.accounts,b,a,'follow');assert out['mutual']
    assert out['counts']['friends']==1
    with pytest.raises(social.PolicyError):social.mutate(s.accounts,a,a,'follow')
    social.mutate(s.accounts,b,a,'block');social.mutate(s.accounts,b,a,'block')
    with social.database(s.accounts) as c:
        assert not social.can_message(c,a,b);assert not social.can_message(c,b,a)
        assert social.view(c,a,b)['counts']==dict(following=0,followers=0,friends=0)
    with pytest.raises(social.PolicyError):social.mutate(s.accounts,a,b,'follow')
    social.mutate(s.accounts,b,a,'unblock');social.mutate(s.accounts,b,a,'unblock')
    with social.database(s.accounts) as c:assert not social.follows(c,a,b);assert not social.follows(c,b,a)


def test_follow_block_concurrency_cannot_leave_blocked_follow(env):
    s,f,u=env;a=u['alice']['subject'];b=u['bob']['subject']
    def execute(action):
        try:social.mutate(s.accounts,a,b,action)
        except social.PolicyError:pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(execute,['follow','block']*20))
    with social.database(s.accounts) as c:assert social.blocked(c,a,b);assert not social.follows(c,a,b)


def test_list_privacy_counts_and_cursor(env):
    s,f,u=env;a=u['alice']['subject'];b=u['bob']['subject']
    for name in ('bob','carol','dave'):social.mutate(s.accounts,a,u[name]['subject'],'follow')
    with social.database(s.accounts) as c:
        assert social.view(c,a,b)['counts']['following']==3
        assert social.view(c,a,b)['lists_visible']['following'] is False
    with pytest.raises(social.PolicyError) as e:social.list_people(s.accounts,a,'following',b)
    assert e.value.code=='YICHAT_LIST_PRIVATE'
    one=social.list_people(s.accounts,a,'following',a,limit=2)
    two=social.list_people(s.accounts,a,'following',a,cursor=one['next_cursor'],limit=2)
    assert len({r['uid'] for r in one['items']+two['items']})==3
    assert two['next_cursor'] is None


@pytest.mark.parametrize('mode', ['everyone','followers','friends'])
def test_message_policy_direction_live_and_block_precedence(env,mode):
    s,f,u=env;a=u['alice']['subject'];b=u['bob']['subject']
    with social.database(s.accounts,True) as c:c.execute('INSERT INTO social_privacy(uid,messages) VALUES(?,?)',(b,mode))
    with social.database(s.accounts) as c:assert social.can_message(c,a,b)==(mode=='everyone')
    social.mutate(s.accounts,a,b,'follow')
    with social.database(s.accounts) as c:assert social.can_message(c,a,b)==(mode!='friends')
    social.mutate(s.accounts,b,a,'follow')
    social.require_message(s.accounts,a,b)
    social.mutate(s.accounts,b,a,'block')
    with pytest.raises(social.PolicyError) as e:social.require_message(s.accounts,a,b)
    assert str(e.value)==social.PRIVACY_MESSAGE
    # Group sending remains permitted.
    policy.require_send(s,{'kind':'group'},u['alice']['mxid'])


def test_precision_public_format():
    assert [social.compact(n) for n in (0,999,1000,1250,999999,1000000,1250000)]==['0','999','1k','1.2k','1m','1m','1.2m']
