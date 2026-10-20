"""Auction financial, deadline, concurrency and privacy tests in temporary DBs."""
import concurrent.futures
import json
import sqlite3
import time
import unittest
from unittest.mock import patch
from flask import Flask, session
import ycards as yc
import test_ycards as base_tests


class AuctionTest(unittest.TestCase):
    setUp = base_tests.CardsTest.setUp
    tearDown = base_tests.CardsTest.tearDown
    connect = base_tests.CardsTest.connect
    formal = base_tests.CardsTest.formal
    start = base_tests.CardsTest.start
    finish = base_tests.CardsTest.finish
    instances = base_tests.CardsTest.instances
    balance = base_tests.CardsTest.balance
    assertError = base_tests.CardsTest.assertError

    def card(self):
        if not self.instances():
            self.formal()
            with patch.object(yc, 'sample_defects', return_value={'layers': [], 'cuts': [], 'destroyed': False}):
                self.start(mode='factory')
            self.finish()
        return self.instances()[0]['id']

    def auction(self, **overrides):
        data = dict(instance_id=self.card(),kind='public',pricing='first',clock='fixed',start_price='100',
                    increment='10',duration_hours=1,extension_hours=0,request_key=yc.uuid.uuid4().hex)
        data.update(overrides)
        return self.store.create_auction('a',data)['id'],data

    def bid(self, aid, user, amount, key=None):
        return self.store.bid(user,dict(auction_id=aid,amount=str(amount),request_key=key or yc.uuid.uuid4().hex))

    def row(self, aid):
        with self.connect() as c:return dict(c.execute('SELECT * FROM yc_auctions WHERE id=?',(aid,)).fetchone())

    def settle(self, aid):
        self.store.settle_auctions(self.row(aid)['ends']+1)

    def test_public_first_price_release_outbid_and_settlement(self):
        aid,_=self.auction();initial={u:self.balance(u) for u in ['a','b','c']}
        self.bid(aid,'b','150');self.assertEqual(self.balance('b'),initial['b']-15100)
        self.bid(aid,'c','200.01');self.assertEqual(self.balance('b'),initial['b']);self.assertEqual(self.store.auction_reserved('c'),20201)
        self.settle(aid);self.settle(aid)
        self.assertEqual(self.balance('a'),initial['a']+20001);self.assertEqual(self.balance('c'),initial['c']-20201)
        self.assertEqual(self.row(aid)['winner'],'c');self.assertEqual(self.row(aid)['fee_cents'],200)
        self.assertEqual(self.store.auction_reserved('c'),0)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT owner FROM yc_instances WHERE id=?',(self.row(aid)['instance'],)).fetchone()[0],'c')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM points_ledger WHERE reason='YCards 拍卖手续费' AND reference=?",(aid,)).fetchone()[0],1)

    def test_second_price_uses_distinct_bidder_and_returns_difference(self):
        aid,_=self.auction(pricing='second');initial={u:self.balance(u) for u in ['a','b','c']}
        self.bid(aid,'b',200);self.bid(aid,'c',400);self.bid(aid,'c',500)
        self.settle(aid)
        self.assertEqual(self.row(aid)['price_cents'],20000)
        self.assertEqual(self.balance('c'),initial['c']-20100);self.assertEqual(self.balance('b'),initial['b'])
        self.assertEqual(self.balance('a'),initial['a']+20000)

    def test_second_price_single_bidder_pays_start(self):
        aid,_=self.auction(pricing='second');before=self.balance('b')
        self.bid(aid,'b',300);self.bid(aid,'b',600);self.settle(aid)
        self.assertEqual(self.row(aid)['price_cents'],10000);self.assertEqual(self.balance('b'),before-10100)

    def test_sealed_tie_priority_and_all_escrow(self):
        aid,_=self.auction(kind='sealed',pricing='second');before={u:self.balance(u) for u in ['b','c']}
        self.bid(aid,'b',250);self.bid(aid,'c',250)
        self.assertEqual(self.store.auction_reserved('b'),25200);self.assertEqual(self.store.auction_reserved('c'),25200)
        self.settle(aid);self.assertEqual(self.row(aid)['winner'],'b');self.assertEqual(self.row(aid)['price_cents'],25000)
        self.assertEqual(self.balance('b'),before['b']-25200);self.assertEqual(self.balance('c'),before['c'])

    def test_sealed_privacy_before_and_after_settlement(self):
        aid,_=self.auction(kind='sealed');self.bid(aid,'b',211);self.bid(aid,'c',399)
        for ended in [False,True]:
            if ended:self.settle(aid)
            owner=self.store.auction_detail(aid,'a')
            self.assertEqual(owner['bids'],[{'amount_cents':39900},{'amount_cents':21100}]);self.assertEqual(owner['winner'],'c' if ended else None)
            for field in owner['bids']:
                self.assertNotIn('user',field);self.assertNotIn('priority',field);self.assertNotIn('created',field)
            other=self.store.auction_detail(aid,'b');anonymous=self.store.auction_detail(aid,None)
            for view in [other,anonymous]:
                self.assertIsNone(view['bids']);self.assertIsNone(view['current_cents']);self.assertIsNone(view['winner']);self.assertIsNone(view['price_cents'])
            self.assertEqual(other['my_bid_cents'],21100);self.assertEqual(other['minimum_cents'],21101)
        self.assertTrue(self.store.auction_detail(aid,'c')['won'])

    def test_public_quote_table_includes_users(self):
        aid,_=self.auction();self.bid(aid,'b',200);self.bid(aid,'c',220)
        view=self.store.auction_detail(aid,None)
        self.assertEqual([b['user'] for b in view['bids']],['c','b']);self.assertEqual(view['current_cents'],22000)

    def test_price_increment_and_self_bids(self):
        aid,_=self.auction();self.assertError(lambda:self.bid(aid,'a',100));self.assertError(lambda:self.bid(aid,'b',99))
        self.bid(aid,'b',100);self.assertError(lambda:self.bid(aid,'c','109.99'));self.bid(aid,'c',110)
        self.assertError(lambda:self.bid(aid,'c',110))

    def test_rolling_and_fixed_extension_rules(self):
        aid,_=self.auction(clock='rolling',rolling_hours=2);end=self.row(aid)['ends']
        with patch('ycards_auctions.time.time',return_value=end-10):self.bid(aid,'b',100)
        self.assertEqual(self.row(aid)['ends'],end-10+7200)
        # A second distinct card tests the fixed clock.
        iid=next(x['id'] for x in self.instances() if x['id']!=self.row(aid)['instance'])
        fixed,_=self.auction(instance_id=iid,duration_hours=10,extension_hours=2)
        end=self.row(fixed)['ends']
        with patch('ycards_auctions.time.time',return_value=end-8000):self.bid(fixed,'b',100)
        self.assertEqual(self.row(fixed)['ends'],end)
        with patch('ycards_auctions.time.time',return_value=end-5):self.bid(fixed,'c',200)
        self.assertEqual(self.row(fixed)['ends'],end-5+7200)

    def test_time_limits_and_invalid_numbers(self):
        self.card()
        for overrides in [dict(kind='sealed',duration_hours=337),dict(clock='rolling',rolling_hours=25),dict(extension_hours=13),dict(duration_hours=0),dict(duration_hours='NaN'),dict(duration_hours=True),dict(increment=101),dict(increment=0),dict(kind='sealed',clock='rolling')]:
            self.assertError(lambda:self.auction(**overrides))
        aid,_=self.auction(duration_hours=336,extension_hours=12)
        self.assertEqual(self.row(aid)['duration_seconds'],336*3600)

    def test_idempotency_create_and_bid(self):
        aid,data=self.auction();self.assertEqual(self.store.create_auction('a',data)['id'],aid)
        self.assertError(lambda:self.store.create_auction('a',dict(data,start_price='200')))
        request_key='one-bid-request-1234';self.bid(aid,'b',100,request_key);balance=self.balance('b')
        self.bid(aid,'b',100,request_key);self.assertEqual(self.balance('b'),balance)
        self.assertError(lambda:self.bid(aid,'b',200,request_key))
        self.settle(aid);after=self.balance('b');self.bid(aid,'b',100,request_key);self.assertEqual(self.balance('b'),after)

    def test_insufficient_balance_and_deadline_do_not_release_other_bid(self):
        aid,_=self.auction();self.bid(aid,'b',100)
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=11000 WHERE user="c"')
        self.assertError(lambda:self.bid(aid,'c',110));self.assertEqual(self.balance('c'),11000);self.assertEqual(self.store.auction_reserved('b'),10100)
        with patch('ycards_auctions.time.time',return_value=self.row(aid)['ends']):self.assertError(lambda:self.bid(aid,'c',120))
        self.assertEqual(self.row(aid)['state'],'sold')

    def test_no_bids_unsold_and_no_transfer(self):
        aid,_=self.auction();before=self.balance('a');iid=self.row(aid)['instance'];self.settle(aid)
        self.assertEqual(self.row(aid)['state'],'unsold');self.assertEqual(self.balance('a'),before)
        self.assertIn(iid,[c['id'] for c in self.instances()])
        self.store.list_card('a',dict(instance_id=iid,price='100'))

    def test_sale_and_auction_exclude_each_other(self):
        iid=self.card();lid=self.store.list_card('a',dict(instance_id=iid,price='100'))['id']
        self.assertError(lambda:self.auction(instance_id=iid))
        self.store.change_listing('a',dict(listing_id=lid,action='withdraw'))
        aid,_=self.auction(instance_id=iid)
        self.assertError(lambda:self.store.list_card('a',dict(instance_id=iid,price='100')))
        self.assertError(lambda:self.auction(instance_id=iid))
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.transaction() as c:c.execute('INSERT INTO yc_listings VALUES(?,?,?,?,?,?,?)',('legacy-worker',iid,'a',100,'active',1,1))

    def test_concurrent_bids_same_price_only_one_wins(self):
        aid,_=self.auction()
        def run(u):
            try:self.bid(aid,u,100);return True
            except yc.CardError:return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:self.assertEqual(sum(pool.map(run,['b','c'])),1)
        self.assertEqual(self.store.auction_reserved('b')+self.store.auction_reserved('c'),10100)

    def test_restart_and_parallel_settlement_only_once(self):
        aid,_=self.auction(kind='sealed',pricing='second');self.bid(aid,'b',500);self.bid(aid,'c',300);initial=self.balance('a')
        recovered=yc.Store(self.connect,self.store.cards);deadline=self.row(aid)['ends']+1
        with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(lambda store:store.settle_auctions(deadline),[self.store,recovered]))
        self.assertEqual(self.balance('a'),initial+30000);self.assertEqual(self.row(aid)['price_cents'],30000)
        self.assertEqual(self.store.auction_reserved('b')+self.store.auction_reserved('c'),0)

    def test_history_and_historical_preview_after_relisting(self):
        aid,_=self.auction();self.bid(aid,'b',100)
        self.assertEqual(self.store.auctions_list('a',{'section':'active'},True)['total'],1)
        self.assertEqual(self.store.auctions_list('b',{'section':'participated'},True)['total'],1)
        self.settle(aid)
        self.assertEqual(self.store.auctions_list('a',{'section':'sold'},True)['total'],1)
        self.assertEqual(self.store.auctions_list(None,{})['total'],0)
        self.assertIn('design',self.store.auction_detail(aid,'a')['card'])
        self.assertEqual(self.store.auction_detail(aid,'b')['card']['cid'],self.instances('b')[0]['cid'])

    def test_unexpected_external_transfer_refunds(self):
        aid,_=self.auction();before=self.balance('b');self.bid(aid,'b',100)
        with self.store.transaction() as c:c.execute('UPDATE yc_instances SET owner="c" WHERE id=?',(self.row(aid)['instance'],))
        self.settle(aid);self.assertEqual(self.row(aid)['state'],'cancelled');self.assertEqual(self.balance('b'),before)

    def test_api_anonymous_read_guest_no_bid_and_csrf(self):
        aid,_=self.auction();self.bid(aid,'b',100)
        app=Flask(__name__,template_folder='/root/yitiao/templates');app.secret_key='test';app.config['TESTING']=True
        yc.init_app(app,lambda:dict(username=session['user'],role=session.get('role','user')) if 'user' in session else None,lambda u:None,self.store,worker=False)
        client=app.test_client()
        self.assertEqual(client.get('/ycards/api/auctions').status_code,200)
        self.assertEqual(client.get('/ycards/api/auctions/'+aid).json['bids'][0]['user'],'b')
        self.assertEqual(client.get('/ycards/api/auction-history').status_code,401)
        self.assertEqual(client.post('/ycards/api/bid',json={}).status_code,401)
        with client.session_transaction() as s:s['user']='c';s['role']='guest';s['yc_csrf']='token'
        self.assertEqual(client.post('/ycards/api/bid',json={},headers={'X-YCards-CSRF':'token'}).status_code,403)
        with client.session_transaction() as s:s['role']='user'
        self.assertEqual(client.post('/ycards/api/bid',json={}).status_code,403)

if __name__=='__main__':unittest.main(verbosity=2)
