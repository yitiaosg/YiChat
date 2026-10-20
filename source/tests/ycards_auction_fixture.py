"""Local-only auction UI fixture. Never registers with the real website."""
import sys, tempfile, time
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from flask import Flask,session,redirect
import points,ycards
temporary=tempfile.TemporaryDirectory(prefix='ycards-auctions-browser-')
root=Path(temporary.name)
points.DB_PATH=root/'userdata.db'
app=Flask(__name__,template_folder=str(BASE/'templates'),static_folder=str(BASE/'static'));app.secret_key='auction-isolated-test'
store=ycards.Store(cards=root/'cards')
users={u:dict(username=u,role='guest' if u=='guest' else 'user') for u in ['creator','buyer','rival','guest']}
for u in users:
 with store.transaction() as c:
  points.ensure_account(c,u);c.execute('UPDATE points_accounts SET balance_cents=100000000 WHERE user=?',(u,))
d=dict(name='极光试拍',material='plastic',orientation='portrait',corners='round',color='color',layers=[dict(id='shine',type='foil',effect='text',position='surface',text='YCARDS',density=6,angle=-25)])
store.save('creator',dict(tid='AuctionDemo',design=d,confirmed=True),True)
p=dict(tid='AuctionDemo',mode='factory',count=1,improvement=0,request_key='fixture-production-123')
p['quote_token']=store.quote('creator',p)['token']
with patch.object(ycards,'sample_defects',return_value={'layers':[],'cuts':[],'destroyed':False}):store.start('creator',p)
store.settle(time.time()+1000)
ycards.init_app(app,lambda:users.get(session.get('user')),lambda u:None,store,worker=True)
@app.route('/test-login/<user>')
def login(user):
 session.clear();session['user']=user;return redirect('/ycards/')
@app.route('/test-expire/<aid>',methods=['POST'])
def expire(aid):
 with store.transaction() as c:c.execute('UPDATE yc_auctions SET ends=? WHERE id=?',(time.time()-1,aid))
 store.settle_auctions();return {'ok':True}
app.run(port=5079,debug=False)
