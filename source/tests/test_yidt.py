import unittest,tempfile,sqlite3,sys,time,json,hashlib,concurrent.futures
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import yidt_core as y
from flask import Flask,session,jsonify
import yidt_bridge

class YIDTTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();d=Path(self.tmp.name)
  self.old=(y.DB_PATH,y.ACCOUNT_DB,y.KEY_PATH);y.DB_PATH=d/'ledger.db';y.ACCOUNT_DB=d/'account.db';y.KEY_PATH=d/'key'
  with sqlite3.connect(y.ACCOUNT_DB) as c:
   c.execute('CREATE TABLE users(username TEXT PRIMARY KEY,role TEXT)');c.executemany('INSERT INTO users VALUES(?,?)',[('a','user'),('b','user'),('g','guest')])
  with y.connect() as c:
   c.execute('CREATE TABLE points_accounts(user TEXT PRIMARY KEY,balance_cents INTEGER,streak INTEGER,last_checkin TEXT)');c.executemany('INSERT INTO points_accounts VALUES(?,?,0,NULL)',[('a',100000),('b',100000),('g',10000)])
  y.migrate()
  with y.connect() as c:self.a,self.b,self.g=[c.execute('SELECT address FROM yidt_members WHERE user=?',(u,)).fetchone()[0] for u in ('a','b','g')]
 def tearDown(self):
  y.DB_PATH,y.ACCOUNT_DB,y.KEY_PATH=self.old;self.tmp.cleanup()
 def tx(self,a,b,n):
  with y.connect() as c:c.execute('BEGIN IMMEDIATE');return y.transfer(c,a,b,n)
 def check(self):
  with y.connect() as c:return y.verify_chain(c)
 def test_migration_idempotent_fixed_supply(self):
  first=self.check();self.assertEqual(y.migrate(),first)
  with y.connect() as c:self.assertEqual(int(y.wallet(c,self.a)['balance']),1000*y.SCALE)
 def test_precision_no_float(self):
  self.assertEqual(y.units('1.1234567890123456789'),11234567890123456)
  self.assertEqual(y.fmt(y.SUPPLY),'4294967296')
  for v in ('NaN','1e5','-1','inf','1.2.3'):self.assertRaises(y.Error,y.units,v)
 def test_gas_normal_and_official(self):
  tx=self.tx(self.a,self.b,10*y.SCALE);self.assertEqual(tx['debit'],'10.00390625');self.assertEqual(tx['credit'],'10')
  tx=self.tx(y.OFFICIAL,self.a,100*y.SCALE);self.assertEqual(tx['credit'],'99.99609375');self.check()
 def test_balance_fallback(self):
  tx=self.tx(self.a,self.b,1000*y.SCALE-1);self.assertEqual(tx['debit'],'1000');self.assertEqual(tx['credit'],'999.99609375');self.check()
 def test_underfunded_rollback(self):
  before=self.check();self.assertRaises(y.Error,self.tx,self.a,self.b,1001*y.SCALE);self.assertEqual(self.check(),before)
 def test_shared_import_and_member_removal(self):
  with y.connect() as c:
   sec=json.loads(y.cipher().decrypt(y.wallet(c,self.a)['secret'].encode()))
   self.assertEqual(y.create_wallet(c,'b',sec['private_key']),self.a)
   c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,0,?)',('a','ycards',self.a,y.stamp()))
   c.execute('DELETE FROM yidt_members WHERE user=? AND address=?',('a',self.a))
   self.assertEqual(y.bound_address(c,'a','ycards'),self.a)
   self.assertEqual(y.create_wallet(c,'a',sec['mnemonic']),self.a)
 def test_limits(self):
  with y.connect() as c:
   self.assertRaises(y.Error,y.create_wallet,c,'g')
   for _ in range(15):y.create_wallet(c,'a')
   self.assertRaises(y.Error,y.create_wallet,c,'a')
 def test_daily_streak_gas_once(self):
  from datetime import datetime,timezone,timedelta
  with y.connect() as c:
   c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,0,?)',('a','daily',self.a,y.stamp()))
   day=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat()
   c.execute('INSERT INTO yidt_daily VALUES(?,?,4,NULL)',('a',day))
  with y.connect() as c:c.execute('BEGIN IMMEDIATE');t=y.daily(c,'a');self.assertEqual(t['credit'],'299.99609375')
  with y.connect() as c:self.assertRaises(y.Error,y.daily,c,'a')
  self.check()
 def test_concurrent_daily(self):
  with y.connect() as c:c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,0,?)',('a','daily',self.a,y.stamp()))
  def claim(_):
   try:
    with y.connect() as c:c.execute('BEGIN IMMEDIATE');y.daily(c,'a')
    return True
   except y.Error:return False
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(claim,range(8))),1)
  self.check()
 def test_hash_tampering(self):
  self.tx(self.a,self.b,y.SCALE)
  with y.connect() as c:c.execute("UPDATE yidt_transactions SET amount='7' WHERE id=(SELECT MAX(id) FROM yidt_transactions)")
  self.assertRaises(y.Error,self.check)
 def test_current_browser_revocation(self):
  with y.connect() as c:
   c.executemany('INSERT INTO yidt_sessions VALUES(?,?,?,0)',[('one','a',time.time()+100),('two','a',time.time()+100)])
   c.execute("UPDATE yidt_sessions SET revoked=1 WHERE sid='one'")
   self.assertFalse(y.sid_valid(c,'one','a'));self.assertTrue(y.sid_valid(c,'two','a'))
 def test_payment_bound_to_payload_and_single_use(self):
  app=Flask(__name__);app.secret_key='test';app.testing=True;yidt_bridge.install(app)
  @app.post('/pay')
  def pay():
   with y.connect() as c:
    c.execute('BEGIN IMMEDIATE');tx=y.service_debit(c,'a','ycards',10*y.SCALE,'test','ref');return jsonify(ok=True,tx=tx)
  client=app.test_client()
  with client.session_transaction() as s:s['user']='a'
  with y.connect() as c:c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,0,?)',('a','ycards',self.a,y.stamp()))
  r=client.post('/pay',json={'id':1});self.assertEqual(r.status_code,402);token=r.json['intent']
  with y.connect() as c:c.execute("UPDATE yidt_intents SET state='approved' WHERE id=?",(token,))
  self.assertEqual(client.post('/pay',json={'id':2},headers={'X-YIDT-Intent':token}).status_code,409)
  self.assertEqual(client.post('/pay',json={'id':1},headers={'X-YIDT-Intent':token}).status_code,200)
  self.assertEqual(client.post('/pay',json={'id':1},headers={'X-YIDT-Intent':token}).status_code,409);self.check()
 def test_escrow_assets_and_delta(self):
  with y.connect() as c:
   c.execute('BEGIN IMMEDIATE');y.transfer(c,self.a,y.OFFICIAL,100*y.SCALE)
   y.escrow_set(c,'ycards','auction','a',self.a,100*y.SCALE)
   assets,held=y.wallet_assets(c,self.a);self.assertEqual(assets,1000*y.SCALE-y.GAS);self.assertEqual(held,100*y.SCALE)
  self.check()


class IntegrationTest(YIDTTest):
 def authorize(self,client,path,data,headers=None):
  headers=headers or {};r=client.post(path,json=data,headers=headers)
  if r.status_code!=402:raise AssertionError((r.status_code,r.get_json()))
  token=r.json['intent']
  with y.connect() as c:c.execute("UPDATE yidt_intents SET state='approved' WHERE id=?",(token,))
  r=client.post(path,json=data,headers=headers|{'X-YIDT-Intent':token})
  if r.status_code!=200:raise AssertionError((r.status_code,r.get_json()))
  return r
 def test_wallet_pages_csrf_pin_and_public_privacy(self):
  import yidt
  yidt.app.testing=True;client=yidt.app.test_client()
  with y.connect() as c:c.execute('INSERT INTO yidt_sessions VALUES(?,?,?,0)',('test','a',time.time()+1000))
  with client.session_transaction() as s:s.update(user='a',sid='test',csrf='csrf')
  for path in ['/', '/wallet/'+self.a, '/wallet/'+self.a+'/send','/wallet/'+self.a+'/receive','/wallet/'+self.a+'/backup','/services/ycards/','/explorer/','/explorer/address/'+self.a]:
   r=client.get(path,base_url='https://localhost');self.assertEqual(r.status_code,200,(path,r.data[:100]))
  self.assertEqual(client.get('/pin/',base_url='https://localhost').status_code,200)
  home=client.get('/',base_url='https://localhost').data
  self.assertIn(b'class="user-chip"',home);self.assertIn(b'class="uname">a',home)
  self.assertIn(b'href="/pin/"',home)
  self.assertNotIn(b'class="user-chip"',client.get('/explorer/').data)
  for value in [self.a[:12],self.a+'x','yidt-abcdefghijkl','yidt-abcdefghijklm','a'*63,'a'*65,'%','wallet 1']:
   self.assertEqual(client.get('/explorer/',query_string={'q':value}).status_code,400,value)
  self.assertEqual(client.get('/explorer/',query_string={'q':y.OFFICIAL}).location,'/explorer/address/'+y.OFFICIAL)
  r=client.post('/wallet/'+self.a,data={'action':'rename','name':'evil'},base_url='https://localhost');self.assertEqual(r.status_code,403)
  tx=self.tx(self.a,self.b,y.SCALE)
  with y.connect() as c:pass
  r=client.get('/explorer/tx/'+tx['hash']);self.assertNotIn(b'note',r.data)
  self.assertEqual(client.get('/explorer/?q='+self.a).status_code,302)
  self.assertEqual(client.get('/explorer/?q='+tx['hash']).location,'/explorer/tx/'+tx['hash'])
 def test_parachute_payment_then_manual_launch(self):
  import types
  with patch.dict(sys.modules,{'tools':types.SimpleNamespace(get_secret_key=lambda:'test')}):import parachute as p
  p.app.testing=True;client=p.app.test_client()
  with client.session_transaction() as s:s['user']='a'
  with y.connect() as c:c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,0,?)',('a','parachute',self.a,y.stamp()))
  r=self.authorize(client,'/games/parachute/api/pay',{'stake':'10.1234567890123456789'})
  self.assertEqual(r.json['state']['status'],'paid');self.assertEqual(r.json['state']['stake'],'10.1234567890123456')
  self.assertEqual(client.get('/games/parachute/api/state').json['state']['status'],'paid')
  r=client.post('/games/parachute/api/start',json={});self.assertEqual(r.json['state']['status'],'active')
  with y.connect() as c:c.execute('UPDATE parachute_rounds SET started_at=?,crash_height=10000',(time.time()-2,))
  r=client.post('/games/parachute/api/jump',json={});self.assertEqual(r.json['state']['status'],'jumped');self.assertTrue(r.json['state']['redeem_tx']);self.check()
 def test_ycards_auction_escrow_and_no_end_debit(self):
  import ycards as yc
  app=Flask('cards',template_folder=str(Path(__file__).resolve().parents[1]/'templates'));app.secret_key='test';app.testing=True
  store=yc.Store(y.connect,Path(self.tmp.name)/'cards')
  yc.init_app(app,lambda:dict(username=session['user'],role='user') if session.get('user') else None,lambda _:None,store=store,worker=False)
  yidt_bridge.install(app);clients={}
  for u,addr in [('a',self.a),('b',self.b)]:
   cl=app.test_client()
   with cl.session_transaction() as s:s.update(user=u,yc_csrf='csrf')
   clients[u]=cl
   with y.connect() as c:c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,0,?)',(u,'ycards',addr,y.stamp()))
  from test_ycards import design
  store.save('a',dict(tid='T',design=design(),confirmed=True),True)
  data=dict(tid='T',mode='factory',count=1,improvement=0,request_key='produce0000000001');data['quote_token']=store.quote('a',data)['token']
  headers={'X-YCards-CSRF':'csrf'}
  self.authorize(clients['a'],'/ycards/api/produce',data,headers)
  store.settle(time.time()+100000)
  with y.connect() as c:iid=c.execute('SELECT id FROM yc_instances LIMIT 1').fetchone()[0]
  data=dict(instance_id=iid,kind='public',pricing='second',clock='fixed',start_price='100',increment='10',duration_hours=1,extension_hours=0,request_key='auction0000000001')
  r=self.authorize(clients['a'],'/ycards/api/auctions',data,headers);aid=r.json['id']
  data=dict(auction_id=aid,amount='150',request_key='bid0000000000001')
  # Route shape is taken from the registered blueprint.
  path='/ycards/api/bid'
  r=self.authorize(clients['b'],path,data,headers)
  with y.connect() as c:
   bal=int(y.wallet(c,self.b)['balance']);held=c.execute('SELECT units FROM yidt_escrow WHERE user=?',('b',)).fetchone()[0];self.assertEqual(int(held),151*y.SCALE)
   c.execute("UPDATE yc_auctions SET ends=? WHERE id=?",(time.time()-1,aid))
   before=c.execute("SELECT count(*) FROM yidt_transactions WHERE source=?",(self.b,)).fetchone()[0]
  store.settle_auctions()
  with y.connect() as c:
   self.assertEqual(c.execute("SELECT count(*) FROM yidt_transactions WHERE source=?",(self.b,)).fetchone()[0],before)
   self.assertEqual(int(y.wallet(c,self.b)['balance'])-bal,50*y.SCALE-y.GAS)
   self.assertEqual(c.execute('SELECT owner FROM yc_instances WHERE id=?',(iid,)).fetchone()[0],'b')
  details=store.auction_detail(aid,'b',include_card=False)
  self.assertEqual({r['label'] for r in details['yidt_receipts']},{'付款详情','收款详情'})
  listing=self.authorize(clients['b'],'/ycards/api/list',dict(instance_id=iid,price='20'),headers).json['id']
  self.authorize(clients['a'],'/ycards/api/buy',dict(listing_id=listing,expected_price_cents=2000),headers)
  self.assertEqual({r['label'] for r in store.history('a')['bought'][0]['yidt_receipts']},{'付款详情','收款详情'})
  self.assertEqual({r['label'] for r in store.history('b')['sold'][0]['yidt_receipts']},{'付款详情','收款详情'})
  self.check()


class SecurityTest(YIDTTest):
 def test_transfer_replay_and_private_note(self):
  import yidt
  yidt.app.testing=True;cl=yidt.app.test_client()
  with y.connect() as c:c.execute('INSERT INTO yidt_sessions VALUES(?,?,?,0)',('sid','a',time.time()+100))
  with cl.session_transaction() as ss:ss.update(user='a',sid='sid',csrf='x')
  cl.get('/wallet/'+self.a+'/send')
  with cl.session_transaction() as ss:token=ss['transfer_token']
  data=dict(csrf='x',token=token,destination=self.b,amount='1',note='private note abc')
  r=cl.post('/wallet/'+self.a+'/send',data=data);self.assertEqual(r.status_code,302)
  chain=self.check();again=cl.post('/wallet/'+self.a+'/send',data=data)
  self.assertEqual(again.location,r.location);self.assertEqual(self.check(),chain)
  self.assertIn(b'private note abc',cl.get(r.location).data)
  self.assertNotIn(b'private note abc',yidt.app.test_client().get(r.location).data)
  data['amount']='2';self.assertEqual(cl.post('/wallet/'+self.a+'/send',data=data).status_code,409)
 def test_locked_authorization_checks_pin_and_binding_version(self):
  import yidt
  from werkzeug.security import generate_password_hash
  yidt.app.testing=True;cl=yidt.app.test_client()
  with y.connect() as c:
   c.execute('INSERT INTO yidt_sessions VALUES(?,?,?,0)',('sid','a',time.time()+100))
   c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,1,?)',('a','ycards',self.a,'version1'))
   c.execute('INSERT INTO yidt_pins(user,hash) VALUES(?,?)',('a',generate_password_hash('123456')))
   c.execute('INSERT INTO yidt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('intent','a','sid','ycards','digest',str(y.SCALE),'test','https://yitiao.org/ycards/',self.a,'pending',time.time(),time.time()+100,None,'version1'))
  with cl.session_transaction() as ss:ss.update(user='a',sid='sid',csrf='x')
  self.assertEqual(cl.post('/authorize/intent',data=dict(csrf='x',pin='000000')).status_code,409)
  with y.connect() as c:self.assertEqual(c.execute('SELECT failures FROM yidt_pins').fetchone()[0],1)
  self.assertEqual(cl.post('/authorize/intent',data=dict(csrf='x',pin='123456')).status_code,302)
  with y.connect() as c:c.execute("UPDATE yidt_bindings SET created='version2'")
  self.assertEqual(cl.get('/authorize/intent').status_code,409)
 def test_audit_detects_posting_tampering(self):
  tx=self.tx(self.a,self.b,y.SCALE)
  with y.connect() as c:
   i=c.execute('SELECT id FROM yidt_transactions WHERE hash=?',(tx['hash'],)).fetchone()[0]
   c.execute('UPDATE yidt_postings SET delta=? WHERE tx=? AND address=?',(str(2*y.SCALE),i,self.b))
  self.assertRaises(y.Error,self.check)

if __name__=='__main__':unittest.main()
