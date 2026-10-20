"""Isolated regression tests; never use production account or points databases."""
import base64
import concurrent.futures
import io
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask, session
from PIL import Image
import points
import ycards as yc


def design(material='plastic', corners='square'):
    return dict(name='测试',material=material,orientation='portrait',corners=corners,color='color',layers=[])


def png(alpha=255):
    im=Image.new('RGBA',(2,2),(20,80,170,alpha)); out=io.BytesIO(); im.save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()


def layer(kind='image', **kw):
    defaults=dict(id='L1',type=kind,effect=yc.EFFECTS[kind][0],side='front',position='surface',distance_mm=0,
                  x=0,y=0,width=53.98,height=85.6,rotation=0,scope='full',image=png())
    defaults.update(kw);return defaults


class CardsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/'userdata.db'
        self.store=yc.Store(self.connect,Path(self.tmp.name)/'cards')
        for u in ['a','b','c']:
            with self.store.transaction() as c:
                points.ensure_account(c,u);c.execute('UPDATE points_accounts SET balance_cents=? WHERE user=?',(10**9,u))
    def tearDown(self):self.tmp.cleanup()
    def connect(self):
        c=sqlite3.connect(self.db,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
    def formal(self,tid='T',user='a',d=None):
        return self.store.save(user,dict(tid=tid,design=d or design(),confirmed=True),True)
    def start(self,tid='T',user='a',mode='personal',count=1,improvement=0,key=None):
        data=dict(tid=tid,mode=mode,count=count,improvement=improvement,request_key=key or yc.uuid.uuid4().hex)
        data['quote_token']=self.store.quote(user,data)['token'];return self.store.start(user,data),data
    def finish(self):self.store.settle(time.time()+1000000)
    def instances(self,user='a'):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM yc_instances WHERE owner=?',(user,))]
    def balance(self,user):
        with self.connect() as c:return c.execute('SELECT balance_cents FROM points_accounts WHERE user=?',(user,)).fetchone()[0]
    def assertError(self,fn):
        with self.assertRaises(yc.CardError):fn()

    def test_cost_once_and_commutative(self):
        d=design('glass');d['color']='mono';d['layers']=[layer(position='front',distance_mm=5),layer('foil',id='f',position='front'),layer('filter',id='n'),layer('filter',id='n2')]
        v=yc.validate_design(d);self.assertEqual(v['cost'],2363) # (100+200+400)*2*.75*1.5*1.5
        d['layers'].reverse();self.assertEqual(yc.validate_design(d)['cost'],2363)
        self.assertEqual(yc.validate_design(design())['cost'],100)
        self.assertEqual(v['layers'][0]['canvas_width_mm'],63.98)
    def test_limits_all_320_supported(self):
        d=design('glass');d['layers']=[layer(k,id=f'{k}{i}',side='back' if i%2 else 'front') for k in yc.EFFECTS for i in range(64)]
        v=yc.validate_design(d);self.assertEqual(len(v['layers']),320);self.formal(d=d)
        d['layers'].append(layer(id='overflow'));self.assertError(lambda:yc.validate_design(d))
    def test_assets_security_and_alpha(self):
        value=yc.asset(png(10),True);im=Image.open(io.BytesIO(base64.b64decode(value.split(',')[1])));self.assertEqual(im.getpixel((0,0)),(255,255,255,255))
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(yc.asset(png(10)).split(',')[1]))).getpixel((0,0))[3],10)
        for svg in ['<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', '<svg><image href="https://evil"/></svg>', '<!DOCTYPE x><svg/>', '<svg style="fill:url(https://evil)"/>']:
            self.assertError(lambda:yc.asset('data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode(),True))
        self.assertError(lambda:yc.asset('../../secret'))
        d=design();d['layers']=[layer('glow',mask=png(),scope='full')];self.assertError(lambda:yc.validate_design(d))
    def test_tid_drafts_immutable_and_permissions(self):
        draft=self.store.save('a',{'design':design()},False);self.assertNotIn('tid',draft['design'])
        self.assertTrue((self.store.cards/f"draft_{draft['draft_id']}.card").exists())
        self.assertError(lambda:self.store.save('b',dict(draft_id=draft['draft_id'],revision=1,design=design()),False))
        formal=self.store.save('a',dict(draft_id=draft['draft_id'],revision=1,design=design(),tid='Case',confirmed=True),True)
        self.formal('case');self.assertError(lambda:self.formal('Case'));self.assertError(lambda:self.formal('../x'))
        self.assertError(lambda:self.start('Case','b'))
        self.assertEqual(self.store.templates('b'),[])
        original=(self.store.cards/'Case.card').read_bytes();self.assertError(lambda:self.formal('Case',d=design('glass')));self.assertEqual(original,(self.store.cards/'Case.card').read_bytes())
    def test_quotes_rounding_and_maturity(self):
        q=yc.production_quote(101,1,'factory',3,1000)
        self.assertEqual([s['cost'] for s in q['sheets']],[400,388,376]);self.assertEqual(q['total'],2164);self.assertEqual(q['seconds'],11.64)
        self.assertEqual(yc.production_quote(101,64,'personal',1,0)['total'],37)
        self.assertError(lambda:yc.production_quote(100,0,'factory',1,999))
    def test_recovery_and_idempotency(self):
        self.formal();before=self.balance('a');job,data=self.start(key='idempotent1234567');self.assertEqual(self.store.start('a',data),job)
        self.assertEqual(self.balance('a'),before-10000)
        self.store=yc.Store(self.connect,self.store.cards);self.finish();self.finish()
        cards=self.instances();self.assertEqual(len(cards),1);self.assertEqual(cards[0]['cid'],'T_0_1_1')
        with self.connect() as c:
            s=c.execute('SELECT * FROM yc_stats').fetchone();self.assertEqual((s['maturity'],s['quantity']),(1,1))
        (self.store.cards/'T.card').unlink();self.store.publish();self.assertTrue((self.store.cards/'T.card').exists())
    def test_factory_sequential_numbers_destroyed_and_stats(self):
        self.formal(d=design('glass'))
        with patch.object(yc,'sample_defects',return_value={'layers':[],'cuts':[],'destroyed':True}):self.start(mode='factory',count=3)
        self.finish();self.assertEqual(self.instances(),[])
        with self.connect() as c:
            self.assertEqual([r[0] for r in c.execute('SELECT production_number FROM yc_sheets ORDER BY ordinal')],[1,2,3])
            s=c.execute('SELECT * FROM yc_stats').fetchone();self.assertEqual((s['maturity'],s['quantity']),(9,48))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_defects').fetchone()[0],1)
        with patch.object(yc,'sample_defects',return_value={'layers':[],'cuts':[],'destroyed':False}):self.start()
        self.finish();self.assertTrue(self.instances()[0]['cid'].endswith('_4'))
    def test_loss_dedup_and_geometry(self):
        self.formal();d={'layers':[{'id':'X','missing':True,'offset':[1,2]}],'cuts':[{'axis':'y','line':1,'missing':True,'offset':2}],'destroyed':False}
        with self.store.transaction() as c:
            a=self.store.defect_number(c,'T',d);b=self.store.defect_number(c,'T',json.loads(yc.canonical(d)));self.assertEqual(a,b)
            self.assertEqual(self.store.defect_number(c,'T',{'layers':[],'cuts':[],'destroyed':False}),0)
        geo=yc.product_geometry(yc.validate_design(design()),d,'factory');self.assertEqual(len(geo),12);self.assertEqual(geo[0]['cells'],[1,5]);self.assertEqual(geo[0]['bounds'][3],171.2)
        self.store.publish();saved=json.loads((self.store.cards/'T_1.card').read_text());self.assertNotIn('design',saved);self.assertNotIn('image',yc.canonical(saved))
        shifted={'layers':[],'cuts':[dict(axis='x',line=1,missing=False,offset=5)],'destroyed':False};geo=yc.product_geometry(yc.validate_design(design()),shifted,'factory');self.assertEqual(geo[0]['bounds'][2],58.98)
    def test_corner_cuts_and_cells_partition(self):
        d=yc.validate_design(design(corners='round'));loss={'layers':[],'cuts':[dict(axis='tl',line=0,missing=True),dict(axis='br',line=0,missing=False,offset=2),dict(axis='x',line=1,missing=True)],'destroyed':False}
        geo=yc.product_geometry(d,loss,'factory');self.assertEqual(geo[0]['corners'],[0,3.18,3.18,3.18]);self.assertEqual(sorted(c for g in geo for c in g['cells']),list(range(1,17)))
        self.assertEqual(len({min(g['cells']) for g in geo}),12)
    def test_transaction_fee_owner_and_concurrent_purchase(self):
        self.formal();self.start();self.finish();card=self.instances()[0];lid=self.store.list_card('a',dict(instance_id=card['id'],price='200.01'))['id'];b0=self.balance('b');a0=self.balance('a')
        self.assertError(lambda:self.store.buy('b',dict(listing_id=lid,expected_price_cents=20000)))
        def buy(user):
            try:return user,self.store.buy(user,dict(listing_id=lid,expected_price_cents=20001))
            except yc.CardError:return user,None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(buy,['b','c']))
        winners=[u for u,r in results if r];self.assertEqual(len(winners),1);winner=winners[0]
        self.assertEqual(self.balance(winner),b0-20201);self.assertEqual(self.balance('a'),a0+20001)
        self.assertEqual(self.instances(winner)[0]['cid'],card['cid'])
        self.assertError(lambda:self.start('T',winner));self.assertError(lambda:self.store.delete('a',dict(tid='T',confirmed=True)))
        with self.connect() as c:
            trade=c.execute('SELECT * FROM yc_trades').fetchone();self.assertEqual(trade['fee_cents'],200);self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_trades').fetchone()[0],1)
        buy(winner);self.assertEqual(self.balance(winner),b0-20201)
    def test_insufficient_balance_rolls_back_whole_trade(self):
        self.formal();self.start();self.finish();card=self.instances()[0];lid=self.store.list_card('a',dict(instance_id=card['id'],price='200'))['id']
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=20000 WHERE user="b"')
        before=self.balance('a');self.assertError(lambda:self.store.buy('b',dict(listing_id=lid,expected_price_cents=20000)))
        self.assertEqual(self.balance('b'),20000);self.assertEqual(self.balance('a'),before);self.assertEqual(len(self.instances()),1)
    def test_listing_reprice_withdraw_owner(self):
        self.formal();self.start();self.finish();card=self.instances()[0];lid=self.store.list_card('a',dict(instance_id=card['id'],price='1'))['id']
        self.assertError(lambda:self.store.change_listing('b',dict(listing_id=lid,action='withdraw')))
        self.store.change_listing('a',dict(listing_id=lid,action='reprice',price='3.50'));self.assertEqual(self.store.history('a')['active'][0]['price_cents'],350)
        self.store.change_listing('a',dict(listing_id=lid,action='withdraw'));self.assertEqual(len(self.store.history('a')['withdrawn']),1)
    def test_global_instance_id_not_cid(self):
        self.formal();self.start();self.finish()
        with self.store.transaction() as c:
            # Authorized historical producers can share TID and local sequence;
            # ownership endpoint still only permits current template owner.
            row=dict(c.execute('SELECT * FROM yc_instances').fetchone());vals=list(row.values());vals[0]='other-instance';vals[3]='b';vals[8]='b'
            c.execute('INSERT INTO yc_instances VALUES(?,?,?,?,?,?,?,?,?,?,?)',vals)
        self.assertEqual(self.instances()[0]['cid'],self.instances('b')[0]['cid']);self.assertNotEqual(self.instances()[0]['id'],self.instances('b')[0]['id'])
    def test_concurrent_jobs_and_stale_quote(self):
        self.formal();q=self.store.quote('a',dict(tid='T',mode='personal',count=1,improvement=0))
        def attempt(i):
            try:return self.start(key='parallel-request-'+str(i))[0]
            except yc.CardError:return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:self.assertEqual(sum(v is not None for v in pool.map(attempt,[1,2])),1)
        self.finish();self.assertError(lambda:self.store.start('a',dict(tid='T',mode='personal',count=1,improvement=0,quote_token=q['token'],request_key='stale-quote-123456')))
    def test_concurrent_settlement_and_defect_reuse(self):
        self.formal(d=design(corners='round'))
        loss={'layers': [], 'cuts': [dict(axis='y',line=1,missing=True,offset=2)], 'destroyed':False}
        with patch.object(yc,'sample_defects',return_value=loss):self.start(mode='factory',count=2)
        other=yc.Store(self.connect,self.store.cards)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda store:store.settle(time.time()+100000), [self.store,other]))
        self.assertEqual(len(self.instances()),24)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_defects').fetchone()[0],1)
            stat=c.execute('SELECT * FROM yc_stats').fetchone();self.assertEqual((stat['maturity'],stat['quantity']),(6,32))
        self.assertEqual(len(list(self.store.cards.glob('T_*.card'))),1)
        self.assertFalse((self.store.cards/'T_0.card').exists())

    def test_maturity_ceiling_personal_and_factory_share(self):
        self.formal()
        with self.store.transaction() as c:c.execute('INSERT INTO yc_stats VALUES(?,?,?,?)',('a','T',63,100))
        self.start();self.finish()
        q=self.store.quote('a',dict(tid='T',mode='factory',count=2,improvement=0))
        self.assertEqual([x['cost'] for x in q['sheets']],[144,144])
        with patch.object(yc,'sample_defects',return_value={'layers':[],'cuts':[],'destroyed':False}):self.start(mode='factory',count=2)
        self.finish()
        with self.connect() as c:
            stat=c.execute('SELECT * FROM yc_stats').fetchone();self.assertEqual((stat['maturity'],stat['quantity']),(64,133))
            self.assertEqual(c.execute('SELECT number FROM yc_sequences').fetchone()[0],3)

    def test_probability_scaling_and_record_both_events(self):
        d=yc.validate_design(dict(design(),layers=[layer()]))
        class FixedRandom:
            def __init__(self):self.n=0
            def random(self):
                self.n+=1
                return .015 if self.n%2 else .009
            def uniform(self,a,b):return b
        normal=yc.sample_defects(d,'factory',0,FixedRandom())
        self.assertTrue(normal['layers'][0]['missing']);self.assertIn('offset',normal['layers'][0])
        self.assertEqual(normal['layers'][0]['offset'],[26.99,42.8])
        self.assertEqual(len(normal['cuts']),6)
        for cut in normal['cuts']:
            self.assertTrue(cut['missing']);self.assertEqual(cut['offset'],d['width_mm']/4 if cut['axis']=='x' else d['height_mm']/4)
        improved=yc.sample_defects(d,'factory',1000,FixedRandom());self.assertTrue(yc.empty_defects(improved))
        personal=yc.sample_defects(d,'personal',0,FixedRandom());self.assertTrue(yc.empty_defects(personal))

    def test_fee_boundary_and_defect_filter_combination(self):
        self.formal();loss={'layers':[dict(id='L',missing=True,offset=[1,1])],'cuts':[],'destroyed':False}
        with patch.object(yc,'sample_defects',return_value=loss):self.start()
        self.finish();card=self.instances()[0];lid=self.store.list_card('a',dict(instance_id=card['id'],price='200'))['id']
        self.assertEqual(self.store.cards_list(None,True,{'defects':'missing_print,skew_print'})['total'],1)
        self.assertEqual(self.store.cards_list(None,True,{'defects':'missing_cut'})['total'],0)
        self.store.buy('b',dict(listing_id=lid,expected_price_cents=20000))
        self.assertEqual(self.store.history('b')['bought'][0]['fee_cents'],100)

    def test_delete_unused_and_stale_draft(self):
        saved=self.store.save('a',dict(design=design()),False)
        updated=self.store.save('a',dict(draft_id=saved['draft_id'],revision=1,design=design()),False)
        self.assertEqual(updated['revision'],2)
        self.assertError(lambda:self.store.save('a',dict(draft_id=saved['draft_id'],revision=1,design=design()),False))
        self.formal();self.assertError(lambda:self.store.delete('a',dict(tid='T',confirmed=True)))
        self.store.archive('a',dict(tid='T',archived=True))
        self.assertTrue(any(t.get('tid')=='T' and t['archived'] for t in self.store.templates('a')))
        self.assertEqual(self.store.templates('a',production=True),[])
        self.assertError(lambda:self.formal())
        self.assertTrue((self.store.cards/'T.card').exists())

    def test_private_design_endpoint_and_small_summary(self):
        self.formal()
        summary=self.store.templates('a',summary=True)
        self.assertEqual(summary[0]['material'],'plastic');self.assertNotIn('design',summary[0])
        self.assertEqual(self.store.design('a',tid='T')['design']['tid'],'T')
        self.assertError(lambda:self.store.design('b',tid='T'))
        self.assertError(lambda:self.store.quote('a',dict(tid=['bad'],mode='personal')))
        self.assertError(lambda:yc.validate_design(dict(design(),material=[])))

    def test_http_auth_guest_csrf(self):
        app=Flask(__name__,template_folder=str(Path(yc.__file__).parent/'templates'));app.secret_key='test';app.config['TESTING']=True
        yc.init_app(app,lambda:dict(username=session['user'],role=session.get('role','user')) if 'user' in session else None,lambda u:None,self.store,worker=False)
        client=app.test_client();self.assertEqual(client.get('/ycards/api/market').status_code,200);r=client.get('/ycards/?tab=create');self.assertEqual(r.status_code,302);self.assertIn('next=',r.location)
        self.assertEqual(client.get('/ycards/api/templates').status_code,401)
        with client.session_transaction() as s:s['user']='a';s['role']='guest'
        r=client.get('/ycards/?tab=create');self.assertTrue(r.location.startswith('/account/?err='));self.assertEqual(client.get('/ycards/api/templates').status_code,403)
        with client.session_transaction() as s:s['role']='user';s['yc_csrf']='csrf'
        self.assertEqual(client.post('/ycards/api/drafts',json={'design':design()}).status_code,403)
        self.assertEqual(client.post('/ycards/api/drafts',json={'design':design()},headers={'X-YCards-CSRF':'csrf'}).status_code,200)

if __name__=='__main__':unittest.main(verbosity=2)
