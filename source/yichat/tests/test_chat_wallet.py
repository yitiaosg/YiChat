import secrets,time,concurrent.futures
import pytest
from fastapi import HTTPException
from backend.chat_wallet import Funds
from backend.db import connect

def setup(env):
 s,_,u=env;f=Funds(s);f.migrate();return s,f,u

def deposit(s,f,u,n='20'):
 op=f.payment(u['alice'],{'amount':n,'key':secrets.token_hex(16)},'deposit')
 with connect(s.ledger) as c:c.execute("UPDATE yidt_intents SET state='approved' WHERE id=(SELECT intent FROM chat_wallet_operations WHERE id=?)",(op['id'],))
 assert f.deposit(u['alice'],op['id'])['state']=='completed'
 assert f.deposit(u['alice'],op['id'])['state']=='completed'
 return op

def create(f,u,kind='direct',amount='1',count=1,key=None):
 return f.create(u['alice'],{'room':'!test:chat.yitiao.org','kind':kind},{'amount':amount,'count':count,'key':key or secrets.token_hex(16)},'bob' if kind=='direct' else None)

def test_deposit_transfer_receive_withdraw_exact_and_idempotent(env):
 s,f,u=setup(env);deposit(s,f,u)
 with connect(s.ledger) as c:
  bank=f.bank(c);assert int(f.y.wallet(c,bank)['balance'])==f.y.units('1020.5');before=c.execute('SELECT count(*) FROM yidt_transactions').fetchone()[0]
 key=secrets.token_hex(16);o=create(f,u,key=key);assert create(f,u,key=key)['id']==o['id'];assert o['incoming_fee']=='0'
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:f.receive(u['bob'],o['id'],o['room']),range(8)))
 assert all(r['state']=='paid' for r in results)
 with connect(s.ledger) as c:
  assert f.balance(c,u['alice']['subject'])==f.y.units('19');assert f.balance(c,u['bob']['subject'])==f.y.units('1');assert c.execute('SELECT count(*) FROM yidt_transactions').fetchone()[0]==before
 request={'amount':'0.1','key':secrets.token_hex(16)};paid=f.payment(u['bob'],request,'withdraw');assert f.payment(u['bob'],request,'withdraw')==paid
 with connect(s.ledger) as c:
  tx=c.execute('SELECT * FROM yidt_transactions WHERE hash=?',(paid['tx'],)).fetchone();assert int(tx['credit'])==f.y.units('0.1');assert int(tx['gas'])==f.y.GAS
  assert f.balance(c,u['bob']['subject'])==f.y.units('0.4')-f.y.GAS
 f.reconcile()

def test_redpacket_expiry_and_insufficient_balance(env):
 s,f,u=setup(env)
 with pytest.raises(HTTPException) as e:create(f,u)
 assert e.value.detail['code']=='YICHAT_INSUFFICIENT_BALANCE'
 deposit(s,f,u);o=create(f,u,'group','6',3);got=f.claim(u['bob'],o['id'],o['room']);assert f.claim(u['bob'],o['id'],o['room'])['my_amount']==got['my_amount']
 with connect(s.ledger) as c:c.execute('UPDATE yichat_orders SET expires=0 WHERE id=?',(o['id'],))
 f.expire();f.expire();f.reconcile()
 with connect(s.ledger) as c:
  assert f.balance(c,'order:'+o['id'])==0;assert sum(int(r[0]) for r in c.execute('SELECT balance FROM chat_wallet_accounts'))==f.y.units('20')

def test_deposit_binding_change_rolls_back(env):
 s,f,u=setup(env);op=f.payment(u['alice'],{'amount':'1','key':secrets.token_hex(16)},'deposit')
 with connect(s.ledger) as c:c.execute("UPDATE yidt_intents SET state='approved' WHERE id=(SELECT intent FROM chat_wallet_operations WHERE id=?)",(op['id'],));c.execute("UPDATE yidt_bindings SET created='changed' WHERE user='alice'")
 with pytest.raises(HTTPException):f.deposit(u['alice'],op['id'])
 with connect(s.ledger) as c:assert f.balance(c,u['alice']['subject'])==0
