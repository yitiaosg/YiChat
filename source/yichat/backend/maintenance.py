"""Explicit operator recovery; never exposed as an HTTP route.

Refunds use the normal escrow outbox and immutable ledger. No balance patches or
changes to historical transaction hashes. Exact order IDs and total are required.
"""
from .db import transaction, connect
from .funds import Funds
from . import policy


def refund_unclaimed_direct(settings, sender, order_ids, expected_total):
    if not order_ids or len(set(order_ids)) != len(order_ids):
        raise ValueError('Unique explicit order IDs required')
    f=Funds(settings);f.require();y=f.y
    uid=policy.identity(settings,sender)['uid']
    ids=[];total=0
    with transaction(settings.ledger) as c:
        y.verify_chain(c)
        for oid in order_ids:
            o=c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone()
            p=c.execute('SELECT sender_uid FROM yichat_order_participants WHERE order_id=?',(oid,)).fetchone()
            if not o or not p or p[0]!=uid or o['user']!=sender or o['kind']!='direct' or not o['deposit']:
                raise ValueError('Order sender/type/deposit mismatch')
            if c.execute("SELECT 1 FROM yichat_payouts WHERE order_id=? AND kind='claim'",(oid,)).fetchone():
                raise ValueError('Recipient already authorized a claim')
            refund=c.execute("SELECT * FROM yichat_payouts WHERE order_id=? AND kind='refund'",(oid,)).fetchone()
            if refund:
                if refund['wallet']!=o['source'] or int(refund['principal'])!=int(o['principal']) or o['state'] not in ('refunding','refunded'):
                    raise ValueError('Existing refund mismatch')
                ids.append(refund['id'])
            else:
                if o['state']!='awaiting_receipt' or int(o['remaining'])!=int(o['principal']):
                    raise ValueError('Order is no longer entirely unclaimed')
                reserve=c.execute("SELECT * FROM yidt_escrow WHERE service='yichat' AND reference=? AND user=?",(oid,sender)).fetchone()
                if not reserve or reserve['address']!=o['source'] or int(reserve['units'])!=int(o['principal'])+y.GAS:
                    raise ValueError('Escrow does not match principal and payout fee')
                f.enqueue(c,o,sender,o['source'],int(o['principal']),'refund',int(reserve['units']))
                ids.append(c.execute("SELECT id FROM yichat_payouts WHERE order_id=? AND kind='refund'",(oid,)).fetchone()[0])
                c.execute("UPDATE yichat_orders SET state='refunding',refund_principal=remaining,remaining='0',left_count=0 WHERE id=?",(oid,))
            total+=int(o['principal'])
        if total!=y.units(expected_total):
            raise ValueError('Explicit total does not match orders; no refunds queued')
    # Crash-safe: repeated invocations reuse the same outbox IDs. Each payout and
    # its escrow release/ledger acknowledgment commit atomically in settle_one.
    for pid in ids:f.settle_one(pid)
    f.reconcile()
    with connect(settings.ledger) as c:
        y.verify_chain(c)
        return [{'order':r['order_id'],'wallet':r['wallet'],'principal':y.fmt(r['principal']),'state':r['state'],'tx':r['tx']} for pid in ids for r in c.execute('SELECT * FROM yichat_payouts WHERE id=?',(pid,))]
