"""Isolated local-only fixture. Never installed as a service."""
import sys,os,tempfile,sqlite3,types,threading,time
from pathlib import Path
from werkzeug.serving import make_server
base=Path(__file__).resolve().parents[1];sys.path.insert(0,str(base))
tmp=tempfile.TemporaryDirectory(prefix='yidt-browser-');d=Path(tmp.name)
os.environ.update(YIDT_DB=str(d/'userdata.db'),YIDT_ACCOUNT_DB=str(d/'account.db'),YIDT_KEY=str(d/'key'),YIDT_IDP_INTERNAL='http://127.0.0.1:5081')
sys.modules['tools']=types.SimpleNamespace(get_secret_key=lambda:'fixture-only-secret')
import yidt_core as y
with y.connect() as c:c.execute('CREATE TABLE points_accounts(user TEXT PRIMARY KEY,balance_cents INTEGER,streak INTEGER,last_checkin TEXT)')
import website
for user,role in [('testuser','user'),('guestuser','guest')]:
 website.create_user(user,'TestPassword123!',role)
 with y.connect() as c:c.execute('INSERT INTO points_accounts VALUES(?,?,0,NULL)',(user,100000))
y.migrate()
website.app.extensions['ycards'].save('testuser',dict(tid='YIDTTEST',design=dict(name='YIDT 测试卡',material='plastic',orientation='portrait',corners='square',color='color',layers=[]),confirmed=True),True)
import parachute,yidt
from flask import session,redirect
@website.app.get('/test-login/<user>')
def test_login(user):
 if user not in ('testuser','guestuser'):return '',403
 website._finish_login(user);return redirect('/account/')
@website.app.get('/test-twofa')
def test_twofa():
 import pyotp
 website._update_user('testuser',email='test@example.invalid',totp_secret=pyotp.random_base32())
 return redirect('/account/')
@website.app.get('/test-code')
def test_code():
 import pyotp
 return {'code':pyotp.TOTP(website.get_user('testuser')['totp_secret']).now()}
@website.app.get('/test-info')
def test_info():
 with y.connect() as c:return {'wallets':[dict(r) for r in c.execute('SELECT user,address FROM yidt_members')], 'chain':y.verify_chain(c)}
for app,port in [(website.app,5081),(parachute.app,5082),(yidt.app,5083)]:
 app.config['TESTING']=True
 server=make_server('127.0.0.1',port,app,threaded=True)
 threading.Thread(target=server.serve_forever,daemon=True).start()
print('YIDT browser fixture ready',flush=True)
threading.Event().wait()
