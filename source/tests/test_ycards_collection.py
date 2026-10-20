"""Destruction, showcase purchase, ownership and auction-cap concurrency."""
import concurrent.futures
import time
import unittest
from flask import Flask
from unittest.mock import patch
import ycards as yc
import test_ycards_cache_fees as base

class CollectionTest(unittest.TestCase):
    def setUp(self):
        base.CacheFeesTest.setUp(self)
        self.points_patch=patch.object(yc.points,'DB_PATH',self.db);self.points_patch.start()
    def tearDown(self):
        self.points_patch.stop();base.CacheFeesTest.tearDown(self)
    connect=base.CacheFeesTest.connect;formal=base.CacheFeesTest.formal;start=base.CacheFeesTest.start
    finish=base.CacheFeesTest.finish;instances=base.CacheFeesTest.instances;balance=base.CacheFeesTest.balance
    assertError=base.CacheFeesTest.assertError;card=base.CacheFeesTest.card;auction=base.CacheFeesTest.auction
    def ids(self):self.card();return [r['id'] for r in self.instances()]
    def destroy(self,ids,user='a'):return self.store.destroy_cards(user,dict(instance_ids=ids,confirmed=True))
    def purchase(self,n,key=None):return self.store.buy_showcase_slot('a',dict(expected_slots=n,request_key=key or yc.uuid.uuid4().hex))

    def test_destroy_batch_and_idempotency(self):
        ids=self.ids()[:3];balance=self.balance('a');stats=self.store.templates('a')[0]
        self.assertError(lambda:self.store.destroy_cards('a',dict(instance_ids=ids)))
        self.assertEqual(self.destroy(ids)['destroyed'],3);self.assertEqual(self.destroy(ids)['destroyed'],0)
        self.assertEqual(self.balance('a'),balance)
        self.assertEqual(self.store.templates('a')[0],stats)
        self.assertFalse(set(ids)&{c['id'] for c in self.store.cards_list('a',False,{})['items']})
        self.assertError(lambda:self.store.preview(ids[0],'a'))
        self.assertError(lambda:self.store.list_card('a',dict(instance_id=ids[0],price=10)))
        self.assertError(lambda:self.store.create_auction('a',self.auction(ids[0])))
        self.assertError(lambda:self.store.set_showcase('a',dict(slot=1,instance_id=ids[0])))
        self.assertError(lambda:self.destroy(ids,user='b'))
        self.assertError(lambda:self.store.delete('a',dict(tid='T')))

    def test_protected_batches_are_atomic(self):
        ids=self.ids();self.store.list_card('a',dict(instance_id=ids[0],price=10));self.store.create_auction('a',self.auction(ids[1]));self.store.set_showcase('a',dict(slot=1,instance_id=ids[2]))
        for protected in ids[:3]:
            self.assertError(lambda:self.destroy([ids[3],protected]))
            self.assertEqual(self.store.preview(ids[3],'a')['id'],ids[3])
        cards={c['id']:c for page in ['1','2'] for c in self.store.cards_list('a',False,{'compact':'1','page':page})['items']}
        self.assertTrue(cards[ids[2]]['showcased'])
        self.store.set_showcase('a',dict(slot=1,instance_id=None));self.assertEqual(self.destroy([ids[2]])['destroyed'],1)

    def test_destroy_retains_transaction_and_auction_history(self):
        iid=self.card();a=self.store.create_auction('a',self.auction(iid));self.store.settle_auctions(time.time()+10000)
        listing=self.store.list_card('a',dict(instance_id=iid,price=100));self.store.buy('b',dict(listing_id=listing['id'],expected_price_cents=10000))
        self.assertEqual(self.destroy([iid],user='b')['destroyed'],1)
        self.assertEqual(len(self.store.history('b')['bought']),1)
        self.assertEqual(len(self.store.history('a')['sold']),1)
        self.assertTrue(self.store.auction_detail(a['id'],'a')['card']['destroyed'])

    def test_destroy_ownership_and_batch_validation(self):
        iid=self.card();self.assertError(lambda:self.destroy([iid],user='b'))
        for value in [[],[iid]*501,'bad',['../secret'],[iid,None]]:
            self.assertError(lambda:self.store.destroy_cards('a',dict(instance_ids=value,confirmed=True)))
        self.assertEqual(self.destroy([iid,iid])['destroyed'],1)

    def test_showcase_default_and_permission_and_removal(self):
        ids=self.ids();self.assertEqual(self.store.showcase('a'),dict(slots=3,cards=[]))
        self.assertError(lambda:self.store.set_showcase('b',dict(slot=1,instance_id=ids[0])))
        self.assertError(lambda:self.store.set_showcase('a',dict(slot=4,instance_id=ids[0])))
        self.store.set_showcase('a',dict(slot=1,instance_id=ids[0]));self.store.set_showcase('a',dict(slot=1,instance_id=ids[0]))
        self.assertError(lambda:self.store.set_showcase('a',dict(slot=2,instance_id=ids[0])))
        self.assertEqual(self.store.render_template('T',ids[0],None)['tid'],'T')
        self.assertError(lambda:self.store.design('b','T',None))
        self.store.set_showcase('a',dict(slot=1,instance_id=ids[1]));self.assertError(lambda:self.store.preview(ids[0],None))
        self.store.set_showcase('a',dict(slot=1,instance_id=None));self.assertEqual(self.store.showcase('a')['cards'],[])

    def test_purchase_exact_price_cap_idempotency(self):
        before=self.balance('a');key='purchase-one-key-123';self.purchase(3,key);self.purchase(3,key)
        self.assertEqual(self.balance('a'),before-1000000)
        self.purchase(4);self.purchase(5);self.assertEqual(self.store.showcase('a')['slots'],6)
        self.assertEqual(self.balance('a'),before-3000000);self.assertError(lambda:self.purchase(5))
        self.assertError(lambda:self.purchase(4,key))
        self.store.set_showcase('a',dict(slot=6,instance_id=self.card()))
        self.assertEqual(self.store.showcase('a')['cards'][0]['slot'],6)

    def test_purchase_insufficient_and_stale_no_debit(self):
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=999999 WHERE user="a"')
        self.assertError(lambda:self.purchase(3));self.assertEqual(self.store.showcase('a')['slots'],3);self.assertEqual(self.balance('a'),999999)
        self.assertError(lambda:self.purchase(4));self.assertEqual(self.balance('a'),999999)

    def test_concurrent_purchase_same_or_different_requests(self):
        before=self.balance('a')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r=list(pool.map(lambda _:self.purchase(3,'same-request-key-123'),range(2)))
        self.assertEqual(r[0],r[1]);self.assertEqual(self.balance('a'),before-1000000)
        def attempt(_):
            try:self.purchase(4);return True
            except yc.CardError:return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(r),1);self.assertEqual(self.balance('a'),before-2000000)

    def test_sale_clears_display_atomically(self):
        iid=self.card();self.store.set_showcase('a',dict(slot=1,instance_id=iid));listing=self.store.list_card('a',dict(instance_id=iid,price=100))
        self.store.buy('b',dict(listing_id=listing['id'],expected_price_cents=10000));self.assertEqual(self.store.showcase('a')['cards'],[])
        self.assertError(lambda:self.destroy([iid]));self.store.set_showcase('b',dict(slot=1,instance_id=iid))
        self.assertTrue(self.store.preview(iid,'b')['showcased'])

    def test_auction_sale_clears_display(self):
        iid=self.card();self.store.set_showcase('a',dict(slot=1,instance_id=iid));a=self.store.create_auction('a',self.auction(iid))
        self.store.bid('b',dict(auction_id=a['id'],amount=100,request_key='showcase-auction-bid'));self.store.settle_auctions(time.time()+10000)
        self.assertEqual(self.store.showcase('a')['cards'],[]);self.assertTrue(self.store.preview(iid,'b')['owned'])

    def test_auction_cap_retry_and_release(self):
        ids=self.ids();data=[self.auction(i) for i in ids[:11]]
        for d in data[:10]:self.store.create_auction('a',d)
        before=self.balance('a');self.store.create_auction('a',data[0]);self.assertError(lambda:self.store.create_auction('a',data[10]));self.assertEqual(self.balance('a'),before)
        self.store.settle_auctions(time.time()+10000);self.store.create_auction('a',data[10]);self.assertEqual(self.balance('a'),before-1000)

    def test_concurrent_tenth_auction_only_one_succeeds(self):
        ids=self.ids()
        for i in ids[:9]:self.store.create_auction('a',self.auction(i))
        before=self.balance('a')
        def attempt(i):
            try:self.store.create_auction('a',self.auction(i));return True
            except yc.CardError:return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:r=list(pool.map(attempt,ids[9:11]))
        self.assertEqual(sum(r),1);self.assertEqual(self.balance('a'),before-1000)

    def test_concurrent_destroy_and_showcase_or_listing(self):
        ids=self.ids()
        for iid,other in [(ids[0],lambda i:self.store.set_showcase('a',dict(slot=1,instance_id=i))),(ids[1],lambda i:self.store.list_card('a',dict(instance_id=i,price=10)))]:
            def attempt(f):
                try:f();return True
                except yc.CardError:return False
            with concurrent.futures.ThreadPoolExecutor(2) as pool:r=list(pool.map(attempt,[lambda:self.destroy([iid]),lambda:other(iid)]))
            self.assertEqual(sum(r),1)

    def test_http_guest_csrf_and_private_management(self):
        iid=self.card();app=Flask(__name__);app.secret_key='temporary'
        user={'username':'a','role':'user'};yc.init_app(app,lambda:user,lambda u:None,self.store,worker=False);client=app.test_client()
        with client.session_transaction() as sess:sess['yc_csrf']='csrf-token'
        self.assertEqual(client.post('/ycards/api/destroy',json={'instance_ids':[iid],'confirmed':True}).status_code,403)
        user['role']='guest';self.assertEqual(client.get('/ycards/api/showcase').status_code,403)
        user['role']='user';self.assertEqual(client.get('/ycards/api/showcase').status_code,200)
        response=client.post('/ycards/api/showcase',headers={'X-YCards-CSRF':'csrf-token'},json={'slot':1,'instance_id':iid});self.assertEqual(response.status_code,200)
        self.assertTrue(client.get('/ycards/api/preview/'+iid+'?compact=1').json['showcased'])

if __name__=='__main__':unittest.main()
