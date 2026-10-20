"""Isolated browser fixture using actual profile route/template AST, without website startup side effects."""
import ast,secrets,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE))
from flask import Flask,session,redirect,request,render_template_string
import points,ycards
root=Path(tempfile.mkdtemp(prefix='ycards-collection-browser-'));points.DB_PATH=root/'userdata.db'
app=Flask(__name__,template_folder=str(BASE/'templates'),static_folder=str(BASE/'static'));app.secret_key='collection-isolated-fixture'
users={u:dict(username=u,role='guest' if u=='guest' else 'user') for u in ['creator','buyer','guest']}
store=ycards.Store(cards=root/'cards')
for u in users:
 with store.transaction() as c:points.ensure_account(c,u);c.execute('UPDATE points_accounts SET balance_cents=100000000 WHERE user=?',(u,))
d=dict(name='光影收藏',material='plastic',orientation='portrait',corners='round',color='color',layers=[dict(id='foil',type='foil',effect='color',position='surface')])
store.save('creator',dict(tid='Gallery',design=d,confirmed=True),True)
data=dict(tid='Gallery',mode='factory',count=2,improvement=0,request_key='collection-fixture-production');data['quote_token']=store.quote('creator',data)['token']
with patch.object(ycards,'sample_defects',return_value={'destroyed':False,'layers':[],'cuts':[]}):store.start('creator',data)
store.settle(time.time()+10000)
with store.connect() as c:ids=[r['id'] for r in c.execute('SELECT id FROM yc_instances ORDER BY acquired DESC,cell')]
store.list_card('creator',dict(instance_id=ids[0],price=100))
store.create_auction('creator',dict(instance_id=ids[1],kind='public',pricing='first',clock='fixed',start_price=100,increment=10,duration_hours=24,request_key='collection-fixture-auction'))
store.set_showcase('creator',dict(slot=1,instance_id=ids[2]))
current=lambda:users.get(session.get('user'))
ycards.init_app(app,current,lambda u:None,store,worker=True)
profiles={u:dict(nickname=u,biography='这是我的卡片收藏。') for u in users}
namespace=dict(app=app,session=session,request=request,redirect=redirect,render_template_string=render_template_string,secrets=secrets,points=points,_current_user=current,get_user=users.get,_get_profile_row=lambda u:profiles[u],_upsert_profile_row=lambda u,n,b:profiles.update({u:dict(nickname=n,biography=b)}),_user_avatar_url=lambda u:None)
names={'_CHANGEPROFILE_HTML','_PROFILE_HTML','_ycards_showcase_boot','change_profile','public_profile'}
tree=ast.parse((BASE/'website.py').read_text());nodes=[n for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name in names) or (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets))]
exec(compile(ast.Module(body=nodes,type_ignores=[]),'website-profile-routes','exec'),namespace)
@app.route('/test-login/<user>')
def login(user):session.clear();session['user']=user;return redirect('/ycards/?tab=mine')
@app.route('/test-logout')
def logout():session.clear();return redirect('/@creator')
@app.route('/account/')
def account():return 'Account'
@app.route('/test-state')
def test_state():
 with store.connect() as c:
  destroyed=c.execute('SELECT COUNT(*) FROM yc_destroyed').fetchone()[0]
 return dict(destroyed=destroyed,showcase=store.showcase('creator'),balance=points.account_view('creator')['balance_cents'],ids=ids)
if __name__=='__main__':app.run(port=5079,debug=False,threaded=True)
