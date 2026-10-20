"""Main-site account and OAuth regression on isolated existing application fixtures."""
import unittest,json,time,re,io
from pathlib import Path
from unittest.mock import patch
exec(compile(Path(__file__).with_name('yidt_browser_fixture.py').read_text().split('for app,port in')[0],str(Path(__file__).with_name('yidt_browser_fixture.py')),'exec'))
import account_social as social

class SocialPages(unittest.TestCase):
 def setUp(self):
  self.c=website.app.test_client();self.c.get('/test-login/testuser',base_url='https://yitiao.org');self.get('/account/')
  self.a=social.get_identity(website.ACCOUNT_DB,username='testuser')['uid']
  self.b=social.get_identity(website.ACCOUNT_DB,username='guestuser')['uid']
  with social.database(website.ACCOUNT_DB,True) as db:
   db.execute('DELETE FROM social_blocks');db.execute('DELETE FROM social_follows');db.execute('DELETE FROM social_privacy')
 def get(self,path):return self.c.get(path,base_url='https://yitiao.org')
 def post(self,path,**kw):
  with self.c.session_transaction(base_url='https://yitiao.org') as ss:csrf=ss['social_csrf']
  return self.c.post(path,base_url='https://yitiao.org',headers={'Origin':'https://yitiao.org','X-CSRF-Token':csrf},**kw)
 def test_account_menu_security_profile_and_self_controls(self):
  body=self.get('/account/').data.decode()
  for text in ('个人资料设置','账户与安全','隐私设置','我的二维码','朋友'):self.assertIn(text,body)
  self.assertNotIn('data-social-follow=',body);self.assertNotIn('data-social-message',body)
  self.assertNotIn('btn-logout',body.split('<body>')[-1])
  security=self.get('/account/security/').data.decode()
  for text in ('登录方式','两步验证','删除账户','退出登录'):self.assertIn(text,security)
  profile=self.get('/account/profile/').data.decode();self.assertIn('昵称与自我介绍',profile)
 def test_public_canonical_routes_and_server_button_state(self):
  for path in ('/@guestuser','/@guestuser/'):
   r=self.get(path);self.assertEqual(r.status_code,200);self.assertIn('发送消息',r.data.decode())
  result=self.post('/api/social/'+self.b+'/follow',json={});self.assertEqual(result.status_code,200)
  self.assertIn('已关注',self.get('/@guestuser/').data.decode())
  social.mutate(website.ACCOUNT_DB,self.b,self.a,'follow')
  self.assertIn('互相关注',self.get('/@guestuser/').data.decode())
  social.mutate(website.ACCOUNT_DB,self.b,self.a,'block')
  self.assertNotIn('data-social-message',self.get('/@guestuser/').data.decode())
 def test_csrf_private_lists_and_blacklist_management(self):
  path='/api/social/'+self.b+'/follow'
  self.assertEqual(self.c.post(path,json={},base_url='https://yitiao.org').status_code,403)
  self.assertEqual(self.get('/api/social/'+self.b+'/lists/followers').status_code,403)
  self.assertEqual(self.get('/api/social/'+self.a+'/lists/followers').status_code,200)
  self.assertEqual(self.post('/api/social/block-username',json={'username':'guestuser'}).status_code,200)
  data=self.get('/api/social/'+self.a+'/lists/blocked').json;self.assertEqual(data['items'][0]['uid'],self.b)
  self.assertEqual(self.post('/api/social/'+self.b+'/unblock',json={}).status_code,200)
 def test_privacy_settings_and_qr_follow_current_username(self):
  r=self.post('/account/privacy/',data={'messages':'friends','following':'on'})
  self.assertEqual(r.status_code,302)
  with social.database(website.ACCOUNT_DB) as db:
   self.assertEqual(social.privacy(db,self.a),dict(messages='friends',following=1,followers=0,friends=0))
  self.assertIn(b'https://yitiao.org/@testuser/',self.get('/account/qr/').data)
  r=self.get('/account/qr/image');self.assertEqual(r.status_code,200);self.assertTrue(r.data.startswith(b'\x89PNG'))
  from PIL import Image
  image=Image.open(io.BytesIO(r.data)).convert('RGBA')
  pixels=set(image.getdata());self.assertEqual({p[3] for p in pixels},{0,255})
  self.assertEqual({p[:3] for p in pixels if p[3]}, {(255,255,255)})
 def test_secondary_menus_only_render_their_own_content(self):
  for path in ['/account/profile/','/account/security/','/account/privacy/','/account/qr/']:
   with self.subTest(path=path):
    body=self.get(path).data.decode()
    for extra in ['acct-head','我的 YIDT','data-count=','yidt-daily','href="/account/privacy/"','href="/account/qr/"']:
     self.assertNotIn(extra,body.split('<body>')[-1])
    self.assertIn('返回账户',body)
  body=self.get('/account/profile/').data.decode()
  self.assertIn('更换头像',body);self.assertIn('昵称与自我介绍',body)
  self.assertNotIn('退出登录',body);self.assertNotIn('账户与安全',body)
 def test_yichat_oidc_stable_sub_and_main_logout_revokes_grants(self):
  user=website.get_user('testuser');claims=website._oidc_claims_for(user)
  self.assertEqual(claims['user_id'],self.a)
  import oidc_provider
  with self.c.session_transaction(base_url='https://yitiao.org') as ss:sid=ss['yidt_sid']
  with website.app.test_request_context('/'):
   with patch.object(oidc_provider,'_sign',side_effect=lambda payload:payload):
    result=oidc_provider._issue_tokens('yichat','testuser','openid profile',int(time.time()),'nonce',sid)
    old=oidc_provider._issue_tokens('yidt','testuser','openid profile',int(time.time()),'nonce',sid)
  self.assertEqual(result['id_token']['sub'],self.a);self.assertEqual(old['id_token']['sub'],'testuser')
  self.assertEqual(self.c.post('/logout/',base_url='https://yitiao.org').status_code,302)
  with y.connect() as db:self.assertFalse(y.sid_valid(db,sid,'testuser'))
  with website._conn() as db:self.assertEqual(db.execute('SELECT count(*) FROM oidc_tokens WHERE sid=?',(sid,)).fetchone()[0],0)

if __name__=='__main__':unittest.main()
