"""Posting fee atomicity and compact, permission-checked render payloads."""
import concurrent.futures
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
import ycards as yc
import test_ycards as base

class CacheFeesTest(unittest.TestCase):
    setUp=base.CardsTest.setUp
    tearDown=base.CardsTest.tearDown
    connect=base.CardsTest.connect
    formal=base.CardsTest.formal
    start=base.CardsTest.start
    finish=base.CardsTest.finish
    instances=base.CardsTest.instances
    balance=base.CardsTest.balance
    assertError=base.CardsTest.assertError

    def card(self):
        if not self.instances():
            self.formal()
            with patch.object(yc,'sample_defects',return_value={'destroyed':False,'layers':[],'cuts':[]}):self.start(mode='factory',count=2)
            self.finish()
        return self.instances()[0]['id']

    def auction(self,iid=None,**kw):
        data=dict(instance_id=iid or self.card(),kind='public',pricing='first',clock='fixed',start_price=100,increment=10,duration_hours=1,request_key=yc.uuid.uuid4().hex)
        data.update(kw);return data

    def test_listing_fee_idempotency_reprice_withdraw_relist(self):
        iid=self.card();initial=self.balance('a');data=dict(instance_id=iid,price=100,request_key='listing-one-request')
        result=self.store.list_card('a',data);self.assertEqual(self.balance('a'),initial-1000)
        self.assertEqual(self.store.list_card('a',data),result);self.assertEqual(self.balance('a'),initial-1000)
        self.assertError(lambda:self.store.list_card('a',dict(data,price=200)))
        self.store.change_listing('a',dict(listing_id=result['id'],action='reprice',price=150))
        self.store.change_listing('a',dict(listing_id=result['id'],action='withdraw'));self.assertEqual(self.balance('a'),initial-1000)
        self.store.list_card('a',dict(data,request_key='listing-two-request'));self.assertEqual(self.balance('a'),initial-2000)
        with self.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM points_ledger WHERE reason='YCards 挂售发布手续费'").fetchone()[0],2)

    def test_listing_insufficient_funds_rolls_back_all(self):
        iid=self.card()
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=999 WHERE user="a"')
        self.assertError(lambda:self.store.list_card('a',dict(instance_id=iid,price=100,request_key='low-balance-request')))
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_listings').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_listing_requests').fetchone()[0],0)
        self.assertEqual(self.balance('a'),999)

    def test_concurrent_listing_requests_charge_once(self):
        iid=self.card();before=self.balance('a');data=dict(instance_id=iid,price=100,request_key='concurrent-request')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.store.list_card('a',data),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(self.balance('a'),before-1000)

    def test_auction_fee_idempotency_and_unsold_no_refund(self):
        data=self.auction();initial=self.balance('a');result=self.store.create_auction('a',data)
        self.assertEqual(self.balance('a'),initial-1000);self.assertEqual(self.store.create_auction('a',data),result)
        self.store.settle_auctions(time.time()+10000);self.assertEqual(self.balance('a'),initial-1000)
        with self.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM points_ledger WHERE reason='YCards 拍卖发布手续费'").fetchone()[0],1)

    def test_auction_insufficient_and_conflict_no_fee(self):
        data=self.auction()
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=999 WHERE user="a"')
        self.assertError(lambda:self.store.create_auction('a',data));self.assertEqual(self.balance('a'),999)
        with self.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_auctions').fetchone()[0],0)
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=10000 WHERE user="a"')
        self.store.list_card('a',dict(instance_id=data['instance_id'],price=100));before=self.balance('a')
        self.assertError(lambda:self.store.create_auction('a',data));self.assertEqual(self.balance('a'),before)

    def test_compact_preview_and_template_permission(self):
        iid=self.card();card=self.store.preview(iid,'a',compact=True)
        self.assertNotIn('design',card);self.assertIn('geometry',card)
        self.assertError(lambda:self.store.render_template('T',iid,None))
        self.assertError(lambda:self.store.render_template('Other',iid,'a'))
        self.store.list_card('a',dict(instance_id=iid,price=100))
        template=self.store.render_template('T',iid,None)
        self.assertEqual(template['tid'],'T');self.assertNotIn('cost',template['design'])
        self.assertError(lambda:self.store.design('b',tid='T'))
        cards=self.store.cards_list('a',False,{'compact':'1','page':'999'})
        self.assertEqual(cards['page'],2);self.assertEqual(len(cards['items']),8)
        self.assertTrue(all('design' not in c and 'geometry' in c and 'defects' in c for c in cards['items']))

    def test_auction_compact_history_and_poll_without_card(self):
        aid=self.store.create_auction('a',self.auction())['id']
        listing=self.store.auctions_list(None,{'compact':'1','page':'999'})
        self.assertEqual(listing['page'],1);self.assertNotIn('design',listing['items'][0]['card'])
        self.assertNotIn('card',self.store.auction_detail(aid,None,compact=True,include_card=False))
        self.store.settle_auctions(time.time()+10000)
        card=self.store.auction_detail(aid,None,compact=True)['card']
        self.assertEqual(self.store.render_template(card['tid'],card['id'],None,card['render_auction_id'])['tid'],'T')

    def test_public_render_endpoint_needs_viewable_instance(self):
        iid=self.card();app=Flask(__name__,template_folder=str(Path(yc.__file__).parent/'templates'));app.secret_key='cache';yc.init_app(app,lambda:None,lambda u:None,self.store,worker=False)
        client=app.test_client();self.assertEqual(client.get('/ycards/').status_code,200);path='/ycards/api/render-template/T?instance_id='+iid
        self.assertEqual(client.get(path).status_code,404)
        self.store.list_card('a',dict(instance_id=iid,price=100))
        response=client.get(path);self.assertEqual(response.status_code,200);self.assertNotIn('cost',response.json['design'])
        self.assertEqual(client.get('/ycards/api/design?tid=T').status_code,401)

if __name__=='__main__':unittest.main()
