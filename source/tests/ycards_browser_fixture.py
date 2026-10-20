import sys, tempfile, sqlite3, time, json
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from flask import Flask,session,redirect
import points, ycards
root=Path(tempfile.mkdtemp(prefix='ycards-browser-'))
points.DB_PATH=root/'userdata.db'
app=Flask(__name__,template_folder=str(BASE/'templates'),static_folder=str(BASE/'static'));app.secret_key='isolated-browser-test'
store=ycards.Store(cards=root/'cards')
users={n:dict(username=n,role='guest' if n=='guest' else 'user') for n in ['creator','buyer','guest']}
for n in users:
 with store.transaction() as c:
  points.ensure_account(c,n);c.execute('UPDATE points_accounts SET balance_cents=100000000 WHERE user=?',(n,))
ycards.init_app(app,lambda:users.get(session.get('user')),lambda u:None,store,worker=True)
@app.route('/test-login/<name>')
def login(name):
 session.clear();session['user']=name;return redirect('/ycards/')
@app.route('/account/')
def acct():return 'Account page'
@app.route('/login/')
def login_page():return 'Login page'
@app.route('/test-data')
def test_data():
 return dict(root=str(root))
app.run(port=5079,debug=False)
