"""v2 effects and promotion transactions, isolated from production data."""
import concurrent.futures
import hashlib
import json
import shutil
import sqlite3
import time
import unittest
from pathlib import Path
from flask import Flask, session
import ycards as yc
import ycards_promotions as promo
import test_ycards as base
from admin_panel import init_admin_panel

class V2Test(unittest.TestCase):
    setUp=base.CardsTest.setUp
    tearDown=base.CardsTest.tearDown
    connect=base.CardsTest.connect
    formal=base.CardsTest.formal
    balance=base.CardsTest.balance
    assertError=base.CardsTest.assertError

    def coupon(self, **kw):
        data=dict(code='SAVE',kind='rate',value='80',max_uses=0,per_user=0)
        data.update(kw);return promo.create(self.store,'admin',data)

    def params(self,user='a',**kw):
        data=dict(tid='T',mode='personal',count=1,improvement=0,coupon='SAVE',request_key=yc.uuid.uuid4().hex)
        data.update(kw);data['quote_token']=self.store.quote(user,data)['token'];return data

    def test_physical_dimensions_and_radius(self):
        d=yc.validate_design(base.design(corners='round'))
        self.assertEqual(d['thickness_mm'],.76);self.assertEqual(d['corner_radius_mm'],3.18)
        self.assertEqual(yc.product_geometry(d,{'cuts':[]},'personal')[0]['corners'],[3.18]*4)
        geo=yc.product_geometry(d,{'cuts':[dict(axis='tl',line=0,missing=True),dict(axis='tr',line=0,offset=.5)]},'factory')[0]
        self.assertEqual(geo['corners'],[0,3.18,3.18,3.18])

    def test_two_masks_cost_all_effect_types(self):
        for kind,effect,expected in [('foil','color',800),('glow','ring',700),('filter','negative',235),('filter','lenticular',235),('material','gold',235),('material','vinyl',313)]:
            d=base.design();d['layers']=[base.layer(kind,effect=effect,scope='custom',mask=base.png(),repeat_mask={'kind':'text','text':'TEST'})]
            self.assertEqual(yc.validate_design(d)['cost'],expected)
        d['layers'][0]['repeat_mask']=None;self.assertEqual(yc.validate_design(d)['cost'],250)
        d['layers'][0]['mask']=None;d['layers'][0]['scope']='full';self.assertEqual(yc.validate_design(d)['cost'],200)

    def test_glow_free_parameters_and_foil_price_parity(self):
        d=base.design();d['layers']=[base.layer('glow',effect='ring',glow_width=12,feather=.2,opacity=.3,tint='#aabbcc')]
        v=yc.validate_design(d);self.assertEqual(v['cost'],300);self.assertEqual(v['layers'][0]['opacity'],.3)
        for effect in ['color','mono']:
            d['layers']=[base.layer('foil',effect=effect)];self.assertEqual(yc.validate_design(d)['cost'],400)

    def test_repeat_validation_and_legacy_conversion(self):
        d=base.design();d['layers']=[base.layer('foil',effect='text',text='OLD',density=7)]
        v=yc.validate_design(d);self.assertEqual(v['layers'][0]['effect'],'color');self.assertEqual(v['layers'][0]['repeat_mask']['text'],'OLD')
        for repeat in [{'kind':'pattern'}, {'density':65},{'size_mm':0},{'text':'中文'},{'font':'url(evil)'},{'angle':float('nan')}]:
            d['layers'][0]=base.layer('filter',repeat_mask=repeat);self.assertError(lambda:yc.validate_design(d))
        d['layers']=[base.layer('glow',tint='url(evil)')];self.assertError(lambda:yc.validate_design(d))

    def test_rate_round_once_time_unchanged_and_improvement_included(self):
        self.formal();self.coupon(value='33.33')
        data=dict(tid='T',mode='factory',count=2,improvement=1000)
        normal=self.store.quote('a',data);q=self.store.quote('a',dict(data,coupon='SAVE'))
        self.assertEqual(q['total'],(normal['total']*3333+9999)//10000)
        self.assertEqual(q['seconds'],normal['seconds']);self.assertEqual(q['sheets'],normal['sheets'])
        self.assertEqual(q['original_total'],1788)

    def test_fixed_cap_and_admin_test_permission(self):
        self.formal();self.coupon(kind='fixed',value=10000)
        q=self.store.quote('a',dict(tid='T',mode='personal',coupon='SAVE'));self.assertEqual(q['total'],0);self.assertEqual(q['seconds'],1)
        self.assertError(lambda:self.store.quote('a',dict(tid='T',mode='personal',coupon='test')))
        self.store.is_admin=lambda u:u=='a'
        data=self.params(coupon='test');before=self.balance('a');job=self.store.start('a',data)
        self.assertEqual(self.balance('a'),before)
        with self.connect() as c:self.assertEqual(c.execute('SELECT paid FROM yc_coupon_uses WHERE job=?',(job['id'],)).fetchone()[0],0)
        self.store.is_admin=lambda u:False
        self.assertError(lambda:self.store.quote('a',dict(tid='T',mode='personal',coupon='test')))

    def test_usage_limit_idempotency_and_failure_rollback(self):
        self.formal();self.coupon(per_user=1,max_uses=1);data=self.params()
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=0 WHERE user="a"')
        self.assertError(lambda:self.store.start('a',data))
        with self.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_coupon_uses').fetchone()[0],0)
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=100000 WHERE user="a"')
        job=self.store.start('a',data);self.assertEqual(self.store.start('a',data),job);self.assertEqual(self.balance('a'),92000)
        self.store.settle(time.time()+100)
        self.assertError(lambda:self.params())
        with self.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_coupon_uses').fetchone()[0],1)

    def test_concurrent_last_coupon_use(self):
        self.formal();self.formal(tid='Other',user='b');self.coupon(max_uses=1)
        a=self.params();b=self.params(user='b',tid='Other')
        def run(pair):
            try:self.store.start(*pair);return 1
            except yc.CardError:return 0
        with concurrent.futures.ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(run,[('a',a),('b',b)])),1)
        with self.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_coupon_uses').fetchone()[0],1)

    def test_disable_expire_stale_quotes_and_reserved_names(self):
        self.formal();self.coupon();data=self.params();promo.toggle(self.store,dict(code='SAVE',active=False))
        self.assertError(lambda:self.store.start('a',data));promo.toggle(self.store,dict(code='SAVE',active=True))
        self.assertError(lambda:self.store.start('a',data))
        with self.store.transaction() as c:c.execute('UPDATE yc_coupons SET expires=? WHERE code="SAVE"',(time.time()-1,))
        self.assertError(lambda:self.params())
        for kw in [dict(code='test'),dict(code='TEST'),dict(code='../x'),dict(code='SAVE'),dict(kind='rate',value='NaN'),dict(kind='fixed',value=-1)]:
            self.assertError(lambda:self.coupon(**kw))

    def test_admin_dashboard_permissions_coupon_and_related_rows(self):
        app=Flask(__name__);app.secret_key='test';app.config['TESTING']=True
        app.extensions['ycards']=self.store
        users={'admin':dict(role='admin'), 'a':dict(role='user')}
        init_admin_panel(app,dict(base=self.tmp.name,get_user=lambda u:users.get(u)))
        client=app.test_client();self.assertEqual(client.get('/admin-panel/api/ycards').status_code,302)
        with client.session_transaction() as s:s['role']='admin';s['user']='admin';s['_admin_csrf']='csrf';s['_admin_grant']='token'
        self.assertEqual(client.get('/admin-panel/api/ycards').status_code,401)
        with sqlite3.connect(Path(self.tmp.name)/'admin_panel.db') as c:c.execute('INSERT INTO grants VALUES(?,?,?)',(hashlib.sha256(b'token').hexdigest(),'admin',time.time()+100))
        response=client.post('/admin-panel/api/ycards/coupons',json=dict(kind='rate',value=80));self.assertEqual(response.status_code,403)
        response=client.post('/admin-panel/api/ycards/coupons',json=dict(code='EIGHTY',kind='rate',value=80),headers={'X-CSRF-Token':'csrf'});self.assertEqual(response.status_code,200)
        overview=client.get('/admin-panel/api/ycards').json;self.assertEqual(overview['counts']['yc_coupons'],1)
        self.formal();rows=client.get('/admin-panel/api/table?db=userdata.db&table=yc_templates&user=a').json['rows'];self.assertEqual(len(rows),1)
        row=client.get('/admin-panel/api/table?db=userdata.db&table=yc_coupons').json['rows'][0];self.assertEqual(row['code'],'EIGHTY');self.assertEqual(row['value'],'支付 80%')
        users['admin']['role']='user';self.assertEqual(client.get('/admin-panel/api/ycards').status_code,302)

if __name__=='__main__':unittest.main()
