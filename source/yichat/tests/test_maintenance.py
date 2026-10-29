import pytest
from backend.maintenance import refund_unclaimed_direct
from backend.db import connect
from test_funds import create,fund


def test_operator_refund_is_exact_and_idempotent(env):
    s,f,u=env
    orders=[fund(env,create(env,kind='direct',amount='0.5')),fund(env,create(env,kind='direct',amount='0.5',key='recovery-second-order'))]
    ids=[o['id'] for o in orders]
    with connect(s.ledger) as c:before=int(f.y.wallet(c,orders[0]['source'])['balance'])
    first=refund_unclaimed_direct(s,'alice',ids,'1')
    assert first==refund_unclaimed_direct(s,'alice',ids,'1')
    assert all(r['state']=='paid' for r in first)
    with connect(s.ledger) as c:
        assert int(f.y.wallet(c,orders[0]['source'])['balance'])-before==f.y.units('1')
        assert c.execute("SELECT count(*) FROM yidt_transactions WHERE kind='yichat_payout'").fetchone()[0]==2
    f.reconcile()


def test_operator_refund_total_sender_or_claim_mismatch_rolls_back(env):
    s,f,u=env;o=fund(env,create(env,kind='direct'))
    for sender,total in [('alice','1000'),('bob',o['amount'])]:
        with pytest.raises(ValueError):refund_unclaimed_direct(s,sender,[o['id']],total)
    with connect(s.ledger) as c:
        assert not c.execute('SELECT 1 FROM yichat_payouts').fetchone()
        assert c.execute('SELECT state FROM yichat_orders').fetchone()[0]=='awaiting_receipt'
    f.receive(u['bob'],o['id'],o['room'])
    with pytest.raises(ValueError):refund_unclaimed_direct(s,'alice',[o['id']],o['amount'])
