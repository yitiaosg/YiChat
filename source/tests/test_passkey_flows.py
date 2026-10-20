import unittest,json,pyotp
from pathlib import Path
from urllib.parse import urlsplit
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from passkey_authenticator import Authenticator
exec(compile(Path(__file__).with_name('yidt_browser_fixture.py').read_text().split('for app,port in')[0],str(Path(__file__).with_name('yidt_browser_fixture.py')),'exec'))

class PasskeyFlows(unittest.TestCase):
 def setUp(self):
  for store in (website.main_passkeys,yidt.wallet_passkeys):
   with store.db() as c:
    c.execute('DELETE FROM passkey_credentials');c.execute('DELETE FROM passkey_users');c.execute('DELETE FROM passkey_challenges')
  website._update_user('testuser',email=None,totp_secret=None,totp_on_login=1)
  with y.connect() as c:c.execute('DELETE FROM yidt_pins');c.execute('DELETE FROM yidt_bindings');c.execute('DELETE FROM yidt_intents')
  self.main=website.app.test_client();self.main.get('/test-login/testuser',base_url='https://yitiao.org');self.main.get('/account/',base_url='https://yitiao.org')
  with self.main.session_transaction(base_url='https://yitiao.org') as ss:self.sid=ss['yidt_sid']
  self.wallet=yidt.app.test_client()
  with self.wallet.session_transaction(base_url=y.ORIGIN) as ss:ss.update(user='testuser',sid=self.sid,csrf='wallet-csrf')
 def json(self,client,path,data,origin='https://yitiao.org'):
  with client.session_transaction(base_url=origin) as ss:csrf=ss.get('_pk_csrf') if origin=='https://yitiao.org' else ss.get('csrf')
  return client.post(path,base_url=origin,json=data,headers={'Origin':origin,'X-Passkey-CSRF':csrf or ''})
 def enroll_main(self):
  secret=pyotp.random_base32();website._update_user('testuser',totp_secret=secret)
  self.assertEqual(self.main.get('/account/passkeys/',base_url='https://yitiao.org').location,'/verify/')
  self.main.get('/verify/',base_url='https://yitiao.org')
  r=self.main.post('/verify/',base_url='https://yitiao.org',data={'_action':'check','code':pyotp.TOTP(secret).now()});self.assertEqual(r.location,'/account/passkeys/')
  self.main.get(r.location,base_url='https://yitiao.org')
  options=self.json(self.main,'/account/passkeys/register/options',{}).json;auth=Authenticator()
  r=self.json(self.main,'/account/passkeys/register/finish',dict(token=options['token'],credential=auth.create(options),name='主站手机'));self.assertEqual(r.status_code,200,r.data)
  return auth
 def enroll_wallet(self):
  from werkzeug.security import generate_password_hash
  with y.connect() as c:c.execute('INSERT INTO yidt_pins(user,hash) VALUES(?,?)',('testuser',generate_password_hash('123456')))
  options=self.json(self.wallet,'/passkeys/register/options',dict(pin='123456'),y.ORIGIN).json;auth=Authenticator()
  r=self.json(self.wallet,'/passkeys/register/finish',dict(token=options['token'],credential=auth.create(options,rp='yidt.yitiao.org',origin=y.ORIGIN),name='钱包手机'),y.ORIGIN);self.assertEqual(r.status_code,200,r.data)
  return auth
 def test_enrollment_requires_non_passkey_twofa(self):
  r=self.main.get('/account/passkeys/',base_url='https://yitiao.org');self.assertIn('/account/?err=',r.location)
  self.assertEqual(self.json(self.main,'/account/passkeys/register/options',{}).status_code,403)
  self.enroll_main();website._update_user('testuser',totp_secret=None)
  self.assertIn('/account/?err=',self.main.get('/account/passkeys/',base_url='https://yitiao.org').location)
  website._update_user('testuser',totp_secret=pyotp.random_base32());self.main.get('/account/passkeys/',base_url='https://yitiao.org')
  body=self.main.get('/verify/?pick=1',base_url='https://yitiao.org').get_data(as_text=True);self.assertNotIn('name="method" value="passkey"',body)
  r=self.main.post('/verify/',base_url='https://yitiao.org',data={'_action':'pick','method':'passkey'});self.assertIn('pick=1',r.location)
  self.assertEqual(self.json(self.main,'/auth/passkeys/verify/options',{}).status_code,400)
 def test_passkey_login_bypasses_second_factor_toggle(self):
  auth=self.enroll_main()
  for toggle in (0,1):
   website._update_user('testuser',totp_on_login=toggle)
   client=website.app.test_client();client.get('/login/',base_url='https://yitiao.org')
   options=self.json(client,'/auth/passkeys/login/options',dict(next='/account/')).json
   r=self.json(client,'/auth/passkeys/login/finish',dict(token=options['token'],context=options['context'],credential=auth.get(options)))
   self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.json['next'],'/account/')
   with client.session_transaction(base_url='https://yitiao.org') as ss:self.assertEqual(ss['user'],'testuser');self.assertNotIn('_v',ss)
 def test_password_login_can_use_passkey_as_second_factor(self):
  auth=self.enroll_main();client=website.app.test_client()
  client.get('/login/',base_url='https://yitiao.org')
  r=client.post('/login/',base_url='https://yitiao.org',data=dict(_action='login',username='testuser',password='TestPassword123!',_next='/account/'))
  self.assertEqual(r.location,'/verify/')
  client.get('/verify/',base_url='https://yitiao.org')
  opts=self.json(client,'/auth/passkeys/verify/options',{}).json
  r=self.json(client,'/auth/passkeys/verify/finish',dict(token=opts['token'],credential=auth.get(opts)))
  self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.json['next'],'/account/')
  self.assertTrue(any('ytdev=' in value for value in r.headers.getlist('Set-Cookie')))
  with client.session_transaction(base_url='https://yitiao.org') as ss:self.assertEqual(ss['user'],'testuser')
 def test_sensitive_passkey_and_method_switch_reject_stale_context(self):
  auth=self.enroll_main()
  with self.main.session_transaction(base_url='https://yitiao.org') as ss:ss['_v']=dict(kind='sensitive',user='testuser',purpose='change_password',next='/account/changepassword/',method='passkey')
  self.main.get('/verify/',base_url='https://yitiao.org')
  opts=self.json(self.main,'/auth/passkeys/verify/options',{}).json
  self.main.post('/verify/',base_url='https://yitiao.org',data=dict(_action='pick',method='totp'))
  self.assertEqual(self.json(self.main,'/auth/passkeys/verify/finish',dict(token=opts['token'],credential=auth.get(opts))).status_code,400)
  self.main.post('/verify/',base_url='https://yitiao.org',data=dict(_action='pick',method='passkey'))
  opts=self.json(self.main,'/auth/passkeys/verify/options',{}).json
  r=self.json(self.main,'/auth/passkeys/verify/finish',dict(token=opts['token'],credential=auth.get(opts)));self.assertEqual(r.json['next'],'/account/changepassword/')
  with self.main.session_transaction(base_url='https://yitiao.org') as ss:self.assertIn('_vok_change_password',ss)
 def test_wallet_pin_required_and_stores_isolated(self):
  self.enroll_main();self.assertFalse(yidt.wallet_passkeys.has('testuser'))
  self.assertEqual(self.json(self.wallet,'/passkeys/register/options',dict(pin='123456'),y.ORIGIN).status_code,400)
  self.enroll_wallet();self.assertEqual(len(website.main_passkeys.items('testuser')),1);self.assertEqual(len(yidt.wallet_passkeys.items('testuser')),1)
  self.assertEqual(self.json(self.wallet,'/passkeys/manage',dict(action='delete',id=yidt.wallet_passkeys.items('testuser')[0]['id'],pin='000000'),y.ORIGIN).status_code,400)
 def test_wallet_transfer_proof_bound_and_pin_fallback(self):
  auth=self.enroll_wallet()
  with y.connect() as c:
   a=c.execute("SELECT address FROM yidt_members WHERE user='testuser' LIMIT 1").fetchone()[0];b=c.execute("SELECT address FROM yidt_members WHERE user='guestuser' LIMIT 1").fetchone()[0]
   c.execute('UPDATE yidt_wallets SET locked=1 WHERE address=?',(a,));balance=int(y.wallet(c,a)['balance'])
  path='/wallet/'+a+'/send';self.wallet.get(path,base_url=y.ORIGIN)
  with self.wallet.session_transaction(base_url=y.ORIGIN) as ss:token=ss['transfer_token']
  fields=dict(token=token,destination=b,amount='10',note='通行密钥转账')
  def proof(fields):
   opts=self.json(self.wallet,'/passkeys/auth/options',dict(path=path,fields=[list(item) for item in fields.items()]),y.ORIGIN).json
   return json.dumps(dict(token=opts['token'],credential=auth.get(opts,rp='yidt.yitiao.org',origin=y.ORIGIN)))
  response=proof(fields)
  r=self.wallet.post(path,base_url=y.ORIGIN,data={**fields,'amount':'11','csrf':'wallet-csrf','passkey_response':response});self.assertEqual(r.status_code,409)
  with y.connect() as c:self.assertEqual(int(y.wallet(c,a)['balance']),balance)
  r=self.wallet.post(path,base_url=y.ORIGIN,data={**fields,'csrf':'wallet-csrf','passkey_response':proof(fields)});self.assertEqual(r.status_code,302,r.data)
  self.wallet.get(path,base_url=y.ORIGIN)
  with self.wallet.session_transaction(base_url=y.ORIGIN) as ss:fields['token']=ss['transfer_token']
  r=self.wallet.post(path,base_url=y.ORIGIN,data={**fields,'csrf':'wallet-csrf','pin':'123456'});self.assertEqual(r.status_code,302,r.data)
  with y.connect() as c:self.assertEqual(int(y.wallet(c,a)['balance']),balance-20*y.SCALE-2*y.GAS);y.verify_chain(c)
 def test_csrf_and_cross_origin_options_rejected(self):
  self.main.get('/login/',base_url='https://yitiao.org')
  self.assertEqual(self.main.post('/auth/passkeys/login/options',base_url='https://yitiao.org',json={},headers={'Origin':'https://evil.invalid'}).status_code,403)
  self.assertEqual(self.wallet.post('/passkeys/auth/options',base_url=y.ORIGIN,json={},headers={'Origin':y.ORIGIN}).status_code,403)

if __name__=='__main__':unittest.main()
