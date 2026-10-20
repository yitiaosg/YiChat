"""Local-only fixture with isolated users, points and admin grant."""
import sys,tempfile,time,hashlib,sqlite3
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from flask import Flask,session,redirect
import points,ycards
from admin_panel import init_admin_panel
root=Path(tempfile.mkdtemp(prefix='ycards-v2-browser-'))
points.DB_PATH=root/'userdata.db'
(root/'templates').symlink_to(BASE/'templates',target_is_directory=True)
app=Flask(__name__,template_folder=str(BASE/'templates'),static_folder=str(BASE/'static'));app.secret_key='v2-isolated-browser-test'
users={n:dict(username=n,role='admin' if n=='creator' else 'guest' if n=='guest' else 'user') for n in ['creator','buyer','guest']}
store=ycards.Store(cards=root/'cards')
for n in users:
 with store.transaction() as c:
  points.ensure_account(c,n);c.execute('UPDATE points_accounts SET balance_cents=100000000 WHERE user=?',(n,))
ycards.init_app(app,lambda:users.get(session.get('user')),lambda u:None,store,worker=True)
init_admin_panel(app,dict(base=str(root),get_user=lambda u:users.get(u)))
@app.route('/test-login/<name>')
def login(name):
 session.clear();session['user']=name;session['role']=users[name]['role'];return redirect('/ycards/')
@app.route('/test-admin')
def admin():
 session.clear();session['user']='creator';session['role']='admin';session['_admin_grant']='fixture-grant'
 with sqlite3.connect(root/'admin_panel.db') as c:
  c.execute('INSERT OR REPLACE INTO grants VALUES(?,?,?)',(hashlib.sha256(b'fixture-grant').hexdigest(),'creator',time.time()+600))
 return redirect('/admin-panel/dashboard/')
@app.route('/account/')
def account():return 'Account'
@app.route('/login/')
def login_page():return 'Login'
app.run(port=5079,debug=False)
