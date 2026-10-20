import time,concurrent.futures,secrets,sqlite3
import pytest
from fastapi import HTTPException
from backend.db import connect,transaction
from backend.funds import draw

def create(env,count=3,amount='1',kind='group',key=None):
    s,f,users=env
    o=f.create(users['alice'],{'room':'!test:chat.yitiao.org','kind':kind},{'key':key or secrets.token_urlsafe(24),'amount':amount,'count':count,'title':'test'},'bob' if kind=='direct' else None)
    return o

def fund(env,o):
    s,f,users=env
    with connect(s.ledger) as c:c.execute("UPDATE yidt_intents SET state='approved' WHERE id=(SELECT intent FROM yichat_orders WHERE id=?)",(o['id'],))
    return f.fund(users['alice'],o['id'])

def test_exact_precision_and_distribution(env):
    s,f,u=env;m=f.y.CENT
    for count in (1,2,3,10,999):
        for extra in (0,1,10**16,12345678901234567890):
            remaining=m*count+extra;total=remaining
            for k in range(count,0,-1):
                n=draw(remaining,k,m);assert m<=n<=remaining-(k-1)*m
                if k>1:assert n<=2*remaining//k
                remaining-=n
            assert remaining==0
    with pytest.raises(ValueError):draw(m,2,m)
    for value in ('0.001','1.00000000000000001','NaN','1e3','-1'):
        with pytest.raises((HTTPException,ValueError)):create(env,2,value)

def test_duplicate_order_and_deposit(env):
    s,f,u=env;key=secrets.token_urlsafe(24);o=create(env,key=key)
    assert create(env,key=key)['id']==o['id']
    with pytest.raises(HTTPException):create(env,key=key,amount='2')
    with pytest.raises(HTTPException):f.fund(u['alice'],o['id'])
    a=fund(env,o);b=f.fund(u['alice'],o['id']);assert a['deposit']==b['deposit']
    with connect(s.ledger) as c:assert c.execute("SELECT COUNT(*) FROM yidt_transactions WHERE kind='yichat_deposit'").fetchone()[0]==1
    f.reconcile()

def test_direct_payout_does_not_reduce_principal(env):
    s,f,u=env;o=fund(env,create(env,amount='0.01',kind='direct'));f.tick()
    assert o['state']=='awaiting_receipt'
    with connect(s.ledger) as c:assert c.execute('SELECT COUNT(*) FROM yichat_payouts').fetchone()[0]==0
    f.reconcile()
    f.receive(u['bob'],o['id'],o['room']);f.tick()
    with connect(s.ledger) as c:
        p=c.execute('SELECT * FROM yichat_payouts').fetchone();tx=c.execute('SELECT * FROM yidt_transactions WHERE hash=?',(p['tx'],)).fetchone()
        assert tx['credit']==str(f.y.CENT)
        assert c.execute('SELECT state FROM yichat_orders').fetchone()[0]=='paid'
    f.reconcile()

def test_direct_only_recipient_in_correct_room_with_unchanged_wallet(env):
    s,f,u=env;o=create(env,kind='direct')
    with pytest.raises(HTTPException):f.receive(u['bob'],o['id'],o['room'])
    o=fund(env,o)
    for who,room in [('alice',o['room']),('carol',o['room']),('bob','!other')]:
        with pytest.raises(HTTPException) as e:f.receive(u[who],o['id'],room)
        assert e.value.status_code==403
    with connect(s.ledger) as c:
        c.execute("UPDATE yidt_bindings SET address=(SELECT address FROM yidt_bindings WHERE user='carol' AND service='yichat') WHERE user='bob'")
    with pytest.raises(HTTPException) as e:f.receive(u['bob'],o['id'],o['room'])
    assert e.value.status_code==409
    with connect(s.ledger) as c:assert c.execute('SELECT COUNT(*) FROM yichat_payouts').fetchone()[0]==0
    f.reconcile()

def test_direct_receipt_is_concurrently_idempotent(env):
    s,f,u=env;o=fund(env,create(env,kind='direct'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:f.receive(u['bob'],o['id'],o['room']),range(8)))
    assert all(r['state']=='paying' for r in results)
    f.tick()
    assert f.receive(u['bob'],o['id'],o['room'])['state']=='paid'
    with connect(s.ledger) as c:
        assert c.execute('SELECT COUNT(*) FROM yichat_payouts').fetchone()[0]==1
        assert c.execute("SELECT COUNT(*) FROM yidt_transactions WHERE kind='yichat_payout'").fetchone()[0]==1
    f.reconcile()

def test_direct_unaccepted_expiry_refunds_full_principal(env):
    s,f,u=env;s.transfer_ttl=60;s.packet_ttl=7200
    before=time.time();o=fund(env,create(env,amount='0.1234567890123456',kind='direct'))
    assert before+60<=o['expires']<=time.time()+60
    with connect(s.ledger) as c:c.execute('UPDATE yichat_orders SET expires=0 WHERE id=?',(o['id'],))
    with pytest.raises(HTTPException):f.receive(u['bob'],o['id'],o['room'])
    f.expire();f.expire();f.tick();f.reconcile()
    with connect(s.ledger) as c:
        payouts=c.execute('SELECT * FROM yichat_payouts').fetchall()
        assert len(payouts)==1 and payouts[0]['kind']=='refund' and payouts[0]['wallet']==o['source']
        tx=c.execute('SELECT credit FROM yidt_transactions WHERE hash=?',(payouts[0]['tx'],)).fetchone()
        assert int(tx[0])==f.y.units(o['amount'])
        assert c.execute('SELECT state FROM yichat_orders').fetchone()[0]=='refunded'
        public=f.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(o['id'],)).fetchone(),'alice')
        assert public['refund']['principal']==str(f.y.units(o['amount']))
        assert public['refund']['amount']==o['amount']
    with pytest.raises(HTTPException):f.receive(u['bob'],o['id'],o['room'])

def test_direct_confirmed_payout_survives_expiry_and_restart(env):
    from backend.funds import Funds
    s,f,u=env;o=fund(env,create(env,kind='direct'));f.receive(u['bob'],o['id'],o['room'])
    with connect(s.ledger) as c:
        pid=c.execute('SELECT id FROM yichat_payouts').fetchone()[0]
        c.execute('UPDATE yichat_orders SET expires=0 WHERE id=?',(o['id'],))
    def crash():raise TimeoutError('lost acknowledgement')
    with pytest.raises(TimeoutError):f.settle_one(pid,crash)
    restarted=Funds(s);restarted.tick();restarted.reconcile()
    with connect(s.ledger) as c:
        payouts=c.execute('SELECT * FROM yichat_payouts').fetchall()
        assert len(payouts)==1 and payouts[0]['kind']=='claim' and payouts[0]['state']=='paid'
        assert c.execute("SELECT COUNT(*) FROM yidt_transactions WHERE kind='yichat_payout'").fetchone()[0]==1

def test_direct_receipt_expiry_race_preserves_principal(env,monkeypatch):
    import backend.funds as funds
    s,f,u=env;o=fund(env,create(env,kind='direct'))
    before=o['expires']-1;after=o['expires']+1
    def now():return before if __import__('threading').current_thread().name.startswith('receipt') else after
    monkeypatch.setattr(funds.time,'time',now)
    def receive():
        try:f.receive(u['bob'],o['id'],o['room'])
        except HTTPException:pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='receipt') as a, concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='expiry') as b:
        tasks=[a.submit(receive),b.submit(f.expire)]
        for task in tasks:task.result()
    f.tick();f.reconcile()
    with connect(s.ledger) as c:
        payouts=c.execute('SELECT * FROM yichat_payouts').fetchall()
        assert len(payouts)==1 and payouts[0]['state']=='paid'
        assert int(payouts[0]['principal'])==f.y.units(o['amount'])

def test_direct_receipt_requires_live_session_and_enabled_funds(env):
    s,f,u=env;o=fund(env,create(env,kind='direct'))
    s.funds_enabled=False
    with pytest.raises(HTTPException) as e:f.receive(u['bob'],o['id'],o['room'])
    assert e.value.status_code==503
    s.funds_enabled=True
    with connect(s.ledger) as c:c.execute("UPDATE yidt_sessions SET revoked=1 WHERE user='bob'")
    with pytest.raises(HTTPException) as e:f.receive(u['bob'],o['id'],o['room'])
    assert e.value.status_code==401

def test_packet_cannot_be_received_as_direct_transfer(env):
    s,f,u=env;o=fund(env,create(env))
    with pytest.raises(HTTPException) as e:f.receive(u['bob'],o['id'],o['room'])
    assert e.value.status_code==403

def test_concurrent_last_packet(env):
    s,f,u=env;o=fund(env,create(env,1,'0.01'))
    def claim(name):
        try:f.claim(u[name],o['id'],o['room']);return True
        except HTTPException:return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:assert sum(pool.map(claim,['bob','carol','dave','eve']))==1
    f.tick();f.reconcile()

def test_user_wallet_unique_and_sender_excluded(env):
    s,f,u=env;o=fund(env,create(env))
    for who,room in [('alice',o['room']),('bob','!other')]:
        with pytest.raises(HTTPException):f.claim(u[who],o['id'],room)
    first=f.claim(u['bob'],o['id'],o['room']);second=f.claim(u['bob'],o['id'],o['room']);assert first['my_amount']==second['my_amount']
    with connect(s.ledger) as c:c.execute("UPDATE yidt_bindings SET address=(SELECT address FROM yidt_bindings WHERE user='bob' AND service='yichat') WHERE user='shared'")
    with pytest.raises(HTTPException):f.claim(u['shared'],o['id'],o['room'])
    f.reconcile()

def test_expiry_refund_excludes_reserved_and_unused_fee_return(env):
    s,f,u=env;o=fund(env,create(env,4,'10'));f.claim(u['bob'],o['id'],o['room'])
    with connect(s.ledger) as c:c.execute('UPDATE yichat_orders SET expires=?',(time.time()-1,))
    with pytest.raises(HTTPException):f.claim(u['carol'],o['id'],o['room'])
    f.expire();f.expire();f.reconcile()
    with connect(s.ledger) as c:
        rows=c.execute('SELECT * FROM yichat_payouts').fetchall();assert len(rows)==2
        assert sum(int(p['principal']) for p in rows)==10*f.y.SCALE
        refund=next(p for p in rows if p['kind']=='refund');assert int(refund['gross'])-f.y.GAS>int(refund['principal'])
    f.tick();f.reconcile()
    with connect(s.ledger) as c:assert c.execute('SELECT state FROM yichat_orders').fetchone()[0]=='refunded'

def test_timeout_rolls_back_payment_and_restart_retries_same_amount(env):
    s,f,u=env;o=fund(env,create(env,1));claim=f.claim(u['bob'],o['id'],o['room'])
    with connect(s.ledger) as c:pid=c.execute('SELECT id FROM yichat_payouts').fetchone()[0]
    def crash():raise TimeoutError('simulate lost ack before commit')
    with pytest.raises(TimeoutError):f.settle_one(pid,crash)
    with connect(s.ledger) as c:assert c.execute("SELECT COUNT(*) FROM yidt_transactions WHERE kind='yichat_payout'").fetchone()[0]==0
    f.settle_one(pid);f.settle_one(pid)
    with connect(s.ledger) as c:
        assert c.execute("SELECT COUNT(*) FROM yidt_transactions WHERE kind='yichat_payout'").fetchone()[0]==1
        assert f.public(c,c.execute('SELECT * FROM yichat_orders').fetchone(),'bob')['my_amount']==claim['my_amount']
    f.reconcile()

def test_changed_binding_and_logout_cannot_fund(env):
    s,f,u=env;o=create(env)
    with connect(s.ledger) as c:
        c.execute("UPDATE yidt_intents SET state='approved'")
        c.execute("UPDATE yidt_bindings SET created='changed' WHERE user='alice'")
    with pytest.raises(HTTPException):f.fund(u['alice'],o['id'])
    with connect(s.ledger) as c:c.execute("UPDATE yidt_sessions SET revoked=1 WHERE user='alice'")
    with pytest.raises(HTTPException):f.fund(u['alice'],o['id'])

def test_disabled_missing_official_and_no_fallback(env):
    s,f,u=env;s.official=''
    with pytest.raises(HTTPException):create(env)
    s.official=f.y.OFFICIAL
    with pytest.raises((ValueError,HTTPException)):create(env,1,'1000')
    with connect(s.ledger) as c:assert c.execute('SELECT COUNT(*) FROM yichat_orders').fetchone()[0]==0

def test_all_claimed_conservation(env):
    s,f,u=env;o=fund(env,create(env,4,'1.1234567890123456'))
    for name in ('bob','carol','dave','eve'):f.claim(u[name],o['id'],o['room'])
    f.tick();f.expire();f.reconcile()
    with connect(s.ledger) as c:
        rows=c.execute('SELECT * FROM yichat_payouts').fetchall()
        assert all(int(p['principal'])>=f.y.CENT for p in rows)
        assert sum(int(p['principal']) for p in rows)==f.y.units('1.1234567890123456')
        assert all(p['kind']=='claim' for p in rows)


def test_pending_membership_and_leave_cannot_win_claim_after_transition(env):
    s,f,u=env;o=fund(env,create(env,1,'0.01'))
    with connect(s.ledger) as c:
        c.execute("UPDATE yichat_memberships SET pending_event='leaving' WHERE room=? AND mxid=?",(o['room'],u['bob']['mxid']))
    with pytest.raises(HTTPException) as error:f.claim(u['bob'],o['id'],o['room'])
    assert error.value.detail['code']=='YICHAT_NOT_CURRENT_MEMBER'
    with connect(s.ledger) as c:
        c.execute("UPDATE yichat_memberships SET membership='leave',pending_event=NULL WHERE room=? AND mxid=?",(o['room'],u['bob']['mxid']))
    with pytest.raises(HTTPException):f.claim(u['bob'],o['id'],o['room'])
    f.claim(u['carol'],o['id'],o['room']);f.tick();f.reconcile()


def test_unknown_payout_never_blindly_repeats_ledger_transfer(env):
    s,f,u=env;o=fund(env,create(env,1));f.claim(u['bob'],o['id'],o['room'])
    with transaction(s.ledger) as c:
        payout=c.execute('SELECT * FROM yichat_payouts').fetchone()
        # Model a recorded authoritative result with a lost/incorrect outbox
        # acknowledgement. This is divergence, not permission to debit again.
        f.y.transfer(c,f.y.OFFICIAL,payout['wallet'],int(payout['gross']),'yichat_payout',payout['id'],'unknown result')
    with pytest.raises(RuntimeError):f.settle_one(payout['id'])
    with connect(s.ledger) as c:
        assert c.execute("SELECT count(*) FROM yidt_transactions WHERE kind='yichat_payout'").fetchone()[0]==1
        assert c.execute('SELECT state FROM yichat_payouts').fetchone()[0]=='pending'


def test_private_funding_rechecks_privacy_inside_authoritative_transaction(env):
    from backend.policy import social
    s,f,u=env;o=create(env,kind='direct')
    with connect(s.ledger) as c:c.execute("UPDATE yidt_intents SET state='approved'")
    social.mutate(s.accounts,u['bob']['subject'],u['alice']['subject'],'block')
    with pytest.raises(HTTPException) as error:f.fund(u['alice'],o['id'])
    assert error.value.status_code==403 and error.value.detail['code']=='YICHAT_MESSAGE_FORBIDDEN'
    with connect(s.ledger) as c:assert c.execute("SELECT count(*) FROM yidt_transactions WHERE kind='yichat_deposit'").fetchone()[0]==0

@pytest.mark.parametrize('kind', ['direct','group'])
@pytest.mark.parametrize('approved', [False,True])
def test_cancel_abandons_unfunded_order_and_authorization(env,kind,approved):
    s,f,u=env;key=secrets.token_urlsafe(24);o=create(env,kind=kind,key=key)
    assert o['actions']['cancel']
    with connect(s.ledger) as c:
        before=c.execute('SELECT count(*) FROM yidt_transactions').fetchone()[0]
        if approved:c.execute("UPDATE yidt_intents SET state='approved'")
        assert not f.public(c,c.execute('SELECT * FROM yichat_orders').fetchone(),'bob')['actions']['cancel']
    with pytest.raises(HTTPException) as error:f.cancel(u['bob'],o['id'])
    assert error.value.status_code==403
    a=f.cancel(u['alice'],o['id']);b=f.cancel(u['alice'],o['id'])
    assert a==b and a['state']=='cancelled' and not a['deposit'] and not a['authorize_url']
    assert not any(a['actions'].values())
    assert create(env,kind=kind,key=key)['id']==o['id']
    assert create(env,kind=kind,key=key)['state']=='cancelled'
    with pytest.raises(HTTPException) as error:f.fund(u['alice'],o['id'])
    assert error.value.status_code==409
    with connect(s.ledger) as c:
        assert c.execute('SELECT state FROM yidt_intents').fetchone()[0]=='cancelled'
        assert c.execute('SELECT count(*) FROM yidt_transactions').fetchone()[0]==before
        assert c.execute("SELECT count(*) FROM yidt_escrow WHERE service='yichat'").fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM yichat_payouts').fetchone()[0]==0
    f.tick();f.reconcile()


def test_cancel_requires_live_member_but_works_with_funding_disabled(env):
    s,f,u=env;o=create(env);s.funds_enabled=False
    with connect(s.ledger) as c:
        assert f.public(c,c.execute('SELECT * FROM yichat_orders').fetchone(),'alice')['actions']['cancel']
        c.execute("UPDATE yichat_memberships SET pending_event='leaving' WHERE mxid=?",(u['alice']['mxid'],))
    with pytest.raises(HTTPException):f.cancel(u['alice'],o['id'])
    with connect(s.ledger) as c:
        c.execute("UPDATE yichat_memberships SET pending_event=NULL WHERE mxid=?",(u['alice']['mxid'],))
        c.execute("UPDATE yidt_sessions SET revoked=1 WHERE user='alice'")
    with pytest.raises(HTTPException) as error:f.cancel(u['alice'],o['id'])
    assert error.value.status_code==401
    with connect(s.ledger) as c:c.execute("UPDATE yidt_sessions SET revoked=0 WHERE user='alice'")
    assert f.cancel(u['alice'],o['id'])['state']=='cancelled'


@pytest.mark.parametrize('kind',['direct','group'])
def test_cancel_and_fund_are_mutually_exclusive(env,kind):
    import threading
    s,f,u=env
    for _ in range(8):
        o=create(env,kind=kind)
        with connect(s.ledger) as c:c.execute("UPDATE yidt_intents SET state='approved' WHERE id=(SELECT intent FROM yichat_orders WHERE id=?)",(o['id'],))
        barrier=threading.Barrier(2)
        def act(method):
            barrier.wait()
            try:return method(u['alice'],o['id'])
            except HTTPException as e:
                assert e.status_code==409
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(act,[f.cancel,f.fund]))
        assert sum(r is not None for r in results)==1
        with connect(s.ledger) as c:
            row=c.execute('SELECT * FROM yichat_orders WHERE id=?',(o['id'],)).fetchone()
            n=c.execute("SELECT count(*) FROM yidt_transactions WHERE kind='yichat_deposit' AND reference=?",(o['id'],)).fetchone()[0]
            assert n==(0 if row['state']=='cancelled' else 1)
        if row['deposit']:
            with pytest.raises(HTTPException) as error:f.cancel(u['alice'],o['id'])
            assert error.value.status_code==409
        f.reconcile()

@pytest.mark.parametrize('kind',['direct','group'])
def test_funded_orders_cannot_be_cancelled(env,kind):
    s,f,u=env;o=fund(env,create(env,kind=kind))
    assert not o['actions']['cancel']
    with pytest.raises(HTTPException) as error:f.cancel(u['alice'],o['id'])
    assert error.value.status_code==409
    with connect(s.ledger) as c:
        current=f.public(c,c.execute('SELECT * FROM yichat_orders WHERE id=?',(o['id'],)).fetchone(),'alice')
        assert current['state']==o['state'] and current['deposit']==o['deposit']
        assert c.execute('SELECT state FROM yidt_intents').fetchone()[0]=='spent'
        assert c.execute('SELECT count(*) FROM yichat_payouts').fetchone()[0]==0
    f.reconcile()
