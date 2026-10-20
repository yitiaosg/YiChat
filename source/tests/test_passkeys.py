import sys,tempfile,sqlite3,unittest,time,concurrent.futures
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import passkeys as pk
from passkey_authenticator import Authenticator

class PasskeyTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'pk.db'
  self.store=pk.Store(self.connect,'yitiao.org','Yitiao',['https://yitiao.org'])
 def tearDown(self):self.tmp.cleanup()
 def connect(self):
  c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row;return c
 def register(self,user='alice',auth=None,options=None,**kwargs):
  auth=auth or Authenticator();options=options or self.store.registration_options(user,'browser')
  with self.store.db() as c:
   c.execute('BEGIN IMMEDIATE')
   try:self.store.register(c,user,'browser',dict(token=options['token'],credential=auth.create(options,**kwargs),name='手机'))
   except pk.Error:c.commit();raise
  return auth
 def finish(self,auth,options,user='alice',sid='browser',purpose='test',context='payment-a',**kwargs):
  with self.store.db() as c:
   c.execute('BEGIN IMMEDIATE')
   try:return self.store.authenticate(c,user,sid,purpose,context,dict(token=options['token'],credential=auth.get(options,**kwargs)))
   except pk.Error:c.commit();raise
 def options(self,user='alice'):return self.store.authentication_options(user,'browser','test','payment-a')
 def test_registration_discoverable_uv_and_direct_login(self):
  opts=self.store.registration_options('alice','browser')
  self.assertEqual(opts['publicKey']['authenticatorSelection']['residentKey'],'required')
  self.assertEqual(opts['publicKey']['authenticatorSelection']['userVerification'],'required')
  auth=self.register(options=opts);options=self.options(None)
  self.assertFalse(options['publicKey'].get('allowCredentials'))
  self.assertEqual(self.finish(auth,options,user=None),'alice')
 def test_non_uv_registration_rejected(self):
  with self.assertRaises(pk.Error):self.register(uv=False)
  self.assertFalse(self.store.has('alice'))
 def test_wrong_registration_origin_rp_and_cross_origin(self):
  for bad in [dict(origin='https://evil.invalid'),dict(rp='yidt.yitiao.org'),dict(cross=True)]:
   with self.assertRaises(pk.Error):self.register(**bad)
 def test_assertion_origin_rp_uv_userhandle_and_signature(self):
  auth=self.register()
  for bad in [dict(origin='https://evil.invalid'),dict(rp='yidt.yitiao.org'),dict(uv=False),dict(cross=True)]:
   with self.assertRaises(pk.Error):self.finish(auth,self.options(),**bad)
  opts=self.options();credential=auth.get(opts);signature=bytearray(pk.decode(credential['response']['signature']));signature[-1]^=1;credential['response']['signature']=pk.encode(bytes(signature))
  with self.store.db() as c:
   with self.assertRaises(pk.Error):self.store.authenticate(c,'alice','browser','test','payment-a',dict(token=opts['token'],credential=credential))
  auth.handle=pk.encode(b'wrong')
  with self.assertRaises(pk.Error):self.finish(auth,self.options())
 def test_replay_expiry_and_counter(self):
  auth=self.register();opts=self.options();self.finish(auth,opts)
  with self.assertRaises(pk.Error):self.finish(auth,opts)
  with self.assertRaises(pk.Error):self.finish(auth,self.options(),count=1)
  opts=self.options()
  with self.store.db() as c:c.execute('UPDATE passkey_challenges SET expires=0 WHERE id=?',(opts['token'],))
  with self.assertRaises(pk.Error):self.finish(auth,opts)
 def test_account_session_purpose_and_payment_context_bound(self):
  auth=self.register();self.register('bob')
  for bad in [dict(user='bob'),dict(sid='another-browser'),dict(purpose='register'),dict(context='payment-b')]:
   with self.assertRaises(pk.Error):self.finish(auth,self.options(),**bad)
  with self.assertRaises(pk.Error):self.finish(auth,self.options('bob'),user='bob')
 def test_delete_revokes_credential_and_challenges(self):
  auth=self.register();opts=self.options()
  with self.store.db() as c:self.store.manage(c,'alice',dict(action='delete',id=pk.encode(auth.id)))
  with self.assertRaises(pk.Error):self.finish(auth,opts)
  self.assertFalse(self.store.has('alice'))
 def test_limit_eight_even_with_concurrent_registration(self):
  registrations=[(Authenticator(),self.store.registration_options('alice','browser')) for _ in range(9)]
  def register(pair):
   try:self.register(auth=pair[0],options=pair[1]);return True
   except pk.Error:return False
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(register,registrations))
  self.assertEqual(sum(results),8);self.assertEqual(len(self.store.items('alice')),8)
  with self.assertRaises(pk.Error):self.store.registration_options('alice','browser')
 def test_other_account_cannot_manage_and_realms_are_distinct(self):
  auth=self.register()
  with self.store.db() as c:
   with self.assertRaises(pk.Error):self.store.manage(c,'bob',dict(action='delete',id=pk.encode(auth.id)))
  wallet=pk.Store(self.connect,'yidt.yitiao.org','YIDT',['https://yidt.yitiao.org'])
  # Even if a credential were copied to another store, its RP signature cannot cross domains.
  opts=wallet.authentication_options('alice','browser','test','payment-a')
  with wallet.db() as c:
   with self.assertRaises(pk.Error):wallet.authenticate(c,'alice','browser','test','payment-a',dict(token=opts['token'],credential=auth.get(opts)))
 def test_form_context_covers_transfer_and_submitter(self):
  from werkzeug.datastructures import MultiDict
  fields=MultiDict([('destination','alice'),('amount','10'),('action','send'),('csrf','x')])
  original=pk.form_context('/wallet/a/send',fields)
  fields['csrf']='changed';fields['passkey_response']='ignored';self.assertEqual(pk.form_context('/wallet/a/send',fields),original)
  fields['amount']='11';self.assertNotEqual(pk.form_context('/wallet/a/send',fields),original)

if __name__=='__main__':unittest.main()
