"""Account PIN continuations, using isolated databases and real TOTP/email verification."""
import unittest,pyotp,re,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
# The fixture prefix creates only temporary databases and never starts a server.
exec(compile(Path(__file__).with_name('yidt_browser_fixture.py').read_text().split('for app,port in')[0],str(Path(__file__).with_name('yidt_browser_fixture.py')),'exec'))

class AccountPinTest(unittest.TestCase):
 def setUp(self):
  self.secret=pyotp.random_base32()
  website._update_user('testuser',email=None,totp_secret=None,google_sub=None)
  with y.connect() as c:
   c.execute('DELETE FROM yidt_account_flows');c.execute('DELETE FROM yidt_pins');c.execute("DELETE FROM yidt_meta WHERE key LIKE 'reauth:%'")
  self.client=website.app.test_client();self.get('/test-login/testuser');self.get('/account/')
  self.wallet_client=yidt.app.test_client()
  with self.client.session_transaction(base_url='https://yitiao.org') as ss:sid=ss['yidt_sid']
  with self.wallet_client.session_transaction(base_url=y.ORIGIN) as ss:ss.update(user='testuser',sid=sid,csrf='wallet-csrf')
 def get(self,path,**kw):
  r=self.client.get(path,base_url='https://yitiao.org',**kw)
  if path.startswith('/account/yidt/pin/'):
   self.assertEqual(r.location,y.ORIGIN+'/pin/?flow='+self.token)
   return self.wallet_client.get(r.location,base_url=y.ORIGIN)
  return r
 def post(self,path,data):
  if path.startswith('/account/yidt/pin/'):
   return self.wallet_client.post('/pin/?flow='+self.token,data={**data,'csrf':'wallet-csrf'},base_url=y.ORIGIN)
  return self.client.post(path,data=data,base_url='https://yitiao.org')
 def csrf(self):
  with self.client.session_transaction(base_url='https://yitiao.org') as ss:return ss['_yidt_csrf']
 def start(self,twofa=False,return_wallet=False):
  if twofa:website._update_user('testuser',email='test@example.invalid',totp_secret=self.secret)
  r=self.get('/account/yidt/verify/'+('?return=yidt' if return_wallet else ''))
  self.token=parse_qs(urlsplit(r.location).query)['flow'][0]
  self.verify='/verify/?flow='+self.token;self.pin='/account/yidt/pin/?flow='+self.token;self.reauth='/account/yidt/verify/?flow='+self.token
  r=self.get(r.location)
  return r
 def choose(self,method):
  r=self.get(self.verify+'&pick=1');self.assertEqual(r.status_code,200)
  self.assertIn(('action="'+self.verify+'"').encode(),r.data)
  r=self.post(self.verify,dict(_action='pick',method=method,csrf=self.csrf()))
  self.assertEqual(r.location,self.verify)
 def save(self,pin='123456'):
  return self.post(self.pin,dict(csrf=self.csrf(),new_pin=pin,confirm_pin=pin))
 def test_switch_methods_error_retry_and_wallet_return(self):
  self.start(True,True);self.choose('totp');self.choose('email');self.choose('totp')
  # A concurrent generic verification must not take over this operation.
  with self.client.session_transaction(base_url='https://yitiao.org') as ss:
   ss['_v']={'kind':'sensitive','user':'testuser','purpose':'change_password','next':'/account/','method':'email'}
  r=self.post(self.verify,dict(_action='check',code='bad',csrf=self.csrf()));self.assertEqual(r.status_code,200)
  r=self.post(self.verify,dict(_action='check',code=pyotp.TOTP(self.secret).now(),csrf=self.csrf()));self.assertEqual(r.location,self.pin)
  r=self.get(r.location);self.assertIn(b'name="new_pin"',r.data)
  r=self.post(self.pin,dict(csrf=self.csrf(),new_pin='123456',confirm_pin='654321'));self.assertEqual(r.status_code,200)
  self.assertIn('两次相同'.encode(),r.data)
  self.assertEqual(self.save().location,'/pin/complete')
  self.assertIn('已过期或已使用'.encode(),self.save('654321').data)
  with y.connect() as c:self.assertTrue(y.pin_ok(c,'testuser','123456'))
 def test_email_switch_and_real_code(self):
  self.start(True);self.choose('totp');self.choose('email')
  with website._conn() as c:
   c.execute('DELETE FROM email_codes WHERE username=?',('testuser',))
  with patch.object(website,'_verify_turnstile',return_value=(True,'')),patch.object(website.mailer,'send_code_email',return_value=(True,'')) as mail:
   r=self.post(self.verify,dict(_action='send',csrf=self.csrf(),**{'cf-turnstile-response':'fixture'}))
   self.assertIn('验证码已发送'.encode(),r.data)
   code=mail.call_args.args[1]
  r=self.post(self.verify,dict(_action='check',code=code,csrf=self.csrf()));self.assertEqual(r.location,self.pin)
  self.assertEqual(self.save().location,'/pin/complete')
 def test_password_and_single_use(self):
  self.start();self.assertNotIn(b'name="new_pin"',self.get(self.pin).data)
  r=self.post(self.reauth,dict(csrf=self.csrf(),password='wrong'));self.assertIn('账户密码不正确'.encode(),r.data)
  r=self.post(self.reauth,dict(csrf=self.csrf(),password='TestPassword123!'));self.assertEqual(r.location,self.pin)
  self.assertEqual(self.save().status_code,302)
  self.assertEqual(self.save('654321').status_code,409)
 def test_pending_foreign_expired_and_csrf_rejected(self):
  self.start(True)
  self.assertEqual(self.save().status_code,409)
  self.assertEqual(self.post(self.verify,dict(_action='pick',method='totp',csrf='wrong')).status_code,403)
  with y.connect() as c:c.execute('UPDATE yidt_account_flows SET expires=0 WHERE token=?',(self.token,))
  self.assertNotIn(b'name="new_pin"',self.get(self.pin).data)
  self.start();self.get('/test-login/guestuser')
  self.assertNotIn(b'name="new_pin"',self.get(self.pin).data)
 def test_google_success_failure_and_new_twofa(self):
  website._update_user('testuser',google_sub='test-google-sub')
  self.start()
  def state():
   with self.client.session_transaction(base_url='https://yitiao.org') as ss:ss['_g_mode']='yidt';ss['_g_next']=self.reauth
  state()
  r=self.get('/login/google/callback/?error=access_denied');self.assertTrue(r.location.startswith(self.reauth+'&error='))
  state()
  with patch.object(website,'_google_fetch_claims',return_value=dict(sub='wrong')):
   self.assertTrue(self.get('/login/google/callback/').location.startswith(self.reauth+'&error='))
  state()
  with patch.object(website,'_google_fetch_claims',return_value=dict(sub='test-google-sub')):
   self.assertEqual(self.get('/login/google/callback/').location,self.pin)
  self.assertEqual(self.save().status_code,302)
  self.start();website._update_user('testuser',totp_secret=self.secret);state()
  with patch.object(website,'_google_fetch_claims',return_value=dict(sub='test-google-sub')):
   self.assertEqual(self.get('/login/google/callback/').location,self.reauth)
  self.assertEqual(self.save().status_code,409)
 def test_cancel_and_regular_verification(self):
  self.start(True)
  r=self.get('/verify/cancel/?flow='+self.token);self.assertIn('/account/?err=',r.location)
  self.assertNotIn(b'name="new_pin"',self.get(self.pin).data)
  with self.client.session_transaction(base_url='https://yitiao.org') as ss:ss['_v']=dict(kind='sensitive',user='testuser',purpose='change_password',next='/account/changepassword/',method='totp')
  r=self.post('/verify/',dict(_action='check',code=pyotp.TOTP(self.secret).now()))
  self.assertEqual(r.location,'/account/changepassword/')

 def test_daily_summary_and_pin_entry_location(self):
  with y.connect() as c:
   c.execute("DELETE FROM yidt_daily WHERE user='testuser'")
   c.execute("DELETE FROM yidt_bindings WHERE user='testuser' AND service='daily'")
   address=c.execute("SELECT address FROM yidt_members WHERE user='testuser' LIMIT 1").fetchone()[0]
  r=self.get('/account/');body=r.get_data(as_text=True)
  self.assertIn('今日可领 99.99609375 YIDT',body);self.assertIn('绑定收款钱包',body)
  self.assertNotIn('gas',body);self.assertNotIn('href="/account/yidt/pin/"',body)
  self.assertIn('支付密码管理',self.wallet_client.get('/',base_url=y.ORIGIN).get_data(as_text=True))
  with y.connect() as c:
   c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,0,?)',('testuser','daily',address,y.stamp()))
   yesterday=(website.datetime.now(website.timezone.utc).date()-website.timedelta(days=1)).isoformat()
   c.execute('INSERT INTO yidt_daily VALUES(?,?,4,NULL)',('testuser',yesterday))
  self.assertIn('今日可领 299.99609375 YIDT',self.get('/account/').get_data(as_text=True))
  r=self.client.post('/account/yidt/daily/',base_url='https://yitiao.org',json={'csrf':self.csrf()});self.assertEqual(r.status_code,200)
  body=self.get('/account/').get_data(as_text=True)
  for text in ('已签','今日已领 299.99609375 YIDT','领取详情','连续 5 天','管理收款钱包'):self.assertIn(text,body)
 def test_explorer_filters_home_but_keeps_public_search(self):
  with y.connect() as c:
   a=c.execute("SELECT address FROM yidt_members WHERE user='testuser' LIMIT 1").fetchone()[0]
   b=c.execute("SELECT address FROM yidt_members WHERE user='guestuser' LIMIT 1").fetchone()[0]
   own=y.transfer(c,y.OFFICIAL,a,y.SCALE,note='服务交易备注')
   unrelated=y.transfer(c,y.OFFICIAL,b,y.SCALE)
  page=self.wallet_client.get('/explorer/',base_url=y.ORIGIN).get_data(as_text=True)
  self.assertIn(own['hash'],page);self.assertNotIn(unrelated['hash'],page);self.assertIn('备注：服务交易备注',page)
  self.assertIn('服务交易备注',self.wallet_client.get('/explorer/tx/'+own['hash'],base_url=y.ORIGIN).get_data(as_text=True))
  anonymous=yidt.app.test_client()
  self.assertNotIn('服务交易备注',anonymous.get('/explorer/tx/'+own['hash'],base_url=y.ORIGIN).get_data(as_text=True))
  page=anonymous.get('/explorer/',base_url=y.ORIGIN).get_data(as_text=True)
  self.assertNotIn(own['hash'],page);self.assertNotIn(unrelated['hash'],page)
  self.assertEqual(anonymous.get('/explorer/?q='+unrelated['hash'],base_url=y.ORIGIN).location,'/explorer/tx/'+unrelated['hash'])
  self.assertEqual(anonymous.get('/explorer/tx/'+unrelated['hash'],base_url=y.ORIGIN).status_code,200)
  self.assertEqual(anonymous.get('/explorer/address/'+b,base_url=y.ORIGIN).status_code,200)
  with self.wallet_client.session_transaction(base_url=y.ORIGIN) as ss:sid=ss['sid']
  with y.connect() as c:c.execute('UPDATE yidt_sessions SET revoked=1 WHERE sid=?',(sid,))
  self.assertNotIn(own['hash'],self.wallet_client.get('/explorer/',base_url=y.ORIGIN).get_data(as_text=True))

if __name__=='__main__':unittest.main()
