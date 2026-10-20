"""Local-only multi-page fixture, never connected to production balances."""
import sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(BASE))
from flask import Flask,session,redirect,request
import points,ycards
root=Path(tempfile.mkdtemp(prefix='ycards-cache-browser-'));points.DB_PATH=root/'userdata.db'
app=Flask(__name__,template_folder=str(BASE/'templates'),static_folder=str(BASE/'static'));app.secret_key='cache-isolated-fixture'
store=ycards.Store(cards=root/'cards');users={u:dict(username=u,role='user') for u in ['creator','buyer','seller0','seller1','seller2','seller3']}
for u in users:
 with store.transaction() as c:points.ensure_account(c,u);c.execute('UPDATE points_accounts SET balance_cents=100000000 WHERE user=?',(u,))
for tid,count in [('CacheA',6),('SlowM',1)]:
 d=dict(name=tid,material='plastic',orientation='portrait',corners='round',color='color',layers=[dict(id='foil',type='foil',effect='color',position='surface')])
 store.save('creator',dict(tid=tid,design=d,confirmed=True),True)
 data=dict(tid=tid,mode='factory',count=count,improvement=0,request_key='production-fixture-'+tid);data['quote_token']=store.quote('creator',data)['token']
 with patch.object(ycards,'sample_defects',return_value={'destroyed':False,'layers':[],'cuts':[]}):store.start('creator',data)
 store.settle(time.time()+10000)
 with store.connect() as c:cards=[dict(r) for r in c.execute('SELECT id FROM yc_instances WHERE tid=? ORDER BY cell,id',(tid,))]
 for i,card in enumerate(cards):
  if (tid=='CacheA' and i<48) or (tid=='SlowM' and i==0):
   result=store.list_card('creator',dict(instance_id=card['id'],price=100))
   if tid=='SlowM':
    with store.transaction() as c:c.execute('UPDATE yc_listings SET updated=0 WHERE id=?',(result['id'],))
  elif tid=='CacheA' and i<80:
   seller='seller'+str((i-48)//10)
   with store.transaction() as c:c.execute('UPDATE yc_instances SET owner=? WHERE id=?',(seller,card['id']))
   store.create_auction(seller,dict(instance_id=card['id'],kind='public',pricing='first',clock='fixed',start_price=100,increment=10,duration_hours=24,request_key='auction-fixture-'+str(i)))
with store.transaction() as c:c.execute("UPDATE yc_instances SET acquired=0 WHERE tid='SlowM'")
@app.before_request
def slow():
 if request.path=='/ycards/api/render-template/SlowM' or request.args.get('name')=='slow-query':time.sleep(1.5)
ycards.init_app(app,lambda:users.get(session.get('user')),lambda u:None,store,worker=True)
@app.route('/test-login/<user>')
def login(user):session.clear();session['user']=user;return redirect('/ycards/')
app.run(port=5079,debug=False,threaded=True)
