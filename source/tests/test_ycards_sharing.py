"""Sharing and bulk listing security/economic invariants; isolated databases only."""
import concurrent.futures
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask,session
import points,ycards as yc
import test_ycards as base

class SharingTest(unittest.TestCase):
    tearDown=base.CardsTest.tearDown
    connect=base.CardsTest.connect
    formal=base.CardsTest.formal
    start=base.CardsTest.start
    finish=base.CardsTest.finish
    instances=base.CardsTest.instances
    balance=base.CardsTest.balance
    assertError=base.CardsTest.assertError
    def setUp(self):
        base.CardsTest.setUp(self);self.formal()
        with patch.object(yc,'sample_defects',return_value={'layers':[],'cuts':[],'destroyed':False}):self.start(mode='factory')
        self.finish();self.cards=self.instances();self.ids=[c['id'] for c in self.cards]
    def batch(self,ids=None,key='batch-list-unique-key',user='a',price='25.50'):
        return self.store.batch_list(user,dict(instance_ids=ids or self.ids[:3],price=price,request_key=key))
    def share(self,i=0,user='a',owner='a'):
        return self.store.share_card(user,dict(instance_id=self.ids[i],expected_owner=owner))
    def public_count(self):
        with self.connect() as c:return c.execute('SELECT COUNT(*) FROM yc_public_cards').fetchone()[0]
    def test_uniform_price_fee_and_idempotency(self):
        before=self.balance('a');result=self.batch();self.assertEqual(result['fee_cents'],3000)
        self.assertEqual(self.balance('a'),before-3000);self.assertEqual(self.batch(ids=list(reversed(self.ids[:3]))) ,result)
        with self.connect() as c:
            rows=c.execute("SELECT price_cents FROM yc_listings WHERE state='active'").fetchall();self.assertEqual([r[0] for r in rows],[2550]*3)
        self.assertError(lambda:self.batch(price='26'));self.assertEqual(self.balance('a'),before-3000)
    def test_insufficient_batch_rolls_back_all_fees_and_listings(self):
        with self.store.transaction() as c:c.execute('UPDATE points_accounts SET balance_cents=2000 WHERE user="a"')
        self.assertError(lambda:self.batch());self.assertEqual(self.balance('a'),2000)
        with self.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM yc_listings').fetchone()[0],0)
    def test_conflict_or_foreign_selection_rolls_back_whole_batch(self):
        lid=self.store.list_card('a',dict(instance_id=self.ids[0],price='30'))['id'];before=self.balance('a')
        self.assertError(lambda:self.batch());self.assertEqual(self.balance('a'),before)
        self.assertError(lambda:self.batch(user='b'))
        self.store.change_listing('a',dict(listing_id=lid,action='withdraw'))
        self.batch()
    def test_concurrent_batch_charged_and_published_once(self):
        before=self.balance('a')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.batch(),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(self.balance('a'),before-3000)
    def test_public_batch_permissions_cancel_and_destroy(self):
        self.assertError(lambda:self.store.set_public('b',dict(instance_ids=self.ids[:2])))
        self.store.set_public('a',dict(instance_ids=self.ids[:2]));self.assertEqual(self.public_count(),2)
        self.assertEqual(self.store.public_card(self.ids[0],self.cards[0]['cid'])['id'],self.ids[0])
        self.store.set_public('a',dict(instance_ids=[self.ids[0]],public=False))
        self.assertError(lambda:self.store.public_card(self.ids[0],self.cards[0]['cid']))
        self.store.destroy_cards('a',dict(instance_ids=[self.ids[1]],confirmed=True));self.assertEqual(self.public_count(),0)
    def test_share_permissions_and_cid_instance_validation(self):
        self.assertError(lambda:self.share(user='b'))
        result=self.share();self.assertIn(self.cards[0]['cid'],result['url']);self.assertIn(self.ids[0],result['url'])
        self.assertError(lambda:self.store.public_card(self.ids[0],self.cards[1]['cid']))
        self.assertError(lambda:self.share(user='b')) # Public does not grant re-publication rights.
        self.store.list_card('a',dict(instance_id=self.ids[0],price='30'));self.assertEqual(self.share(user='b')['url'],result['url'])
    def test_sale_revokes_link_and_stale_view_cannot_reopen(self):
        self.share();lid=self.store.list_card('a',dict(instance_id=self.ids[0],price='30'))['id']
        self.store.buy('b',dict(listing_id=lid,expected_price_cents=3000))
        self.assertEqual(self.public_count(),0);self.assertError(lambda:self.share());self.assertError(lambda:self.share(user='b'))
        self.assertError(lambda:self.store.public_card(self.ids[0],self.cards[0]['cid']))
        self.share(user='b',owner='b');self.assertEqual(self.public_count(),1)
    def test_auction_revokes_and_sealed_buyer_visible_only_to_seller(self):
        aid=self.store.create_auction('a',dict(instance_id=self.ids[0],kind='sealed',pricing='first',clock='fixed',start_price=10,duration_hours=1,request_key='sealed-share-test'))['id']
        self.share(user='c');self.store.bid('b',dict(auction_id=aid,amount=20,request_key='sealed-bid-test-123'))
        self.store.settle_auctions(time.time()+4000);self.assertEqual(self.public_count(),0)
        seller=self.store.auction_detail(aid,'a');anonymous=self.store.auction_detail(aid,None)
        self.assertEqual(seller['winner'],'b');self.assertIsNone(anonymous['winner']);self.assertIsNone(anonymous['card']['owner'])
        self.assertTrue(all('user' not in bid for bid in seller['bids']))
    def test_share_transfer_race_always_ends_private(self):
        lid=self.store.list_card('a',dict(instance_id=self.ids[0],price='30'))['id']
        def share():
            try:self.share(user='c')
            except yc.CardError:pass
        def buy():self.store.buy('b',dict(listing_id=lid,expected_price_cents=3000))
        with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(lambda f:f(),[share,buy]))
        self.assertEqual(self.public_count(),0)
    def test_http_public_get_profiles_csrf_and_revocation(self):
        app=Flask(__name__,template_folder=str(Path(yc.__file__).parent/'templates'));app.secret_key='test';app.testing=True
        yc.init_app(app,lambda:dict(username=session['user'],role=session.get('role','user')) if session.get('user') else None,lambda u:'/static/avatar/'+u+'.jpg',self.store,worker=False,profile=lambda u:dict(nickname='昵称 '+u))
        client=app.test_client();link=self.share()['url']
        self.assertEqual(client.get(link).status_code,200);self.assertEqual(client.get(link).headers['Cache-Control'],'private, no-store')
        self.assertEqual(client.get('/ycards/api/public/'+self.ids[0]+'/'+self.cards[0]['cid']).status_code,200)
        self.assertEqual(client.post('/ycards/api/public',json=dict(instance_ids=self.ids[:2])).status_code,401)
        lid=self.store.list_card('a',dict(instance_id=self.ids[0],price='30'))['id']
        listing=client.get('/ycards/api/market?compact=1').json['items'][0];self.assertEqual(listing['seller_profile']['nickname'],'昵称 a');self.assertEqual(listing['seller_profile']['url'],'/@a')
        with client.session_transaction() as s:s['user']='a';s['yc_csrf']='token'
        self.assertEqual(client.post('/ycards/api/list-batch',json={}).status_code,403)
        self.store.buy('b',dict(listing_id=lid,expected_price_cents=3000))
        history=client.get('/ycards/api/history').json;self.assertEqual(history['sold'][0]['buyer_profile']['username'],'b')
        self.assertEqual(client.get(link).status_code,404)
    def test_overlapping_concurrent_batches_do_not_partially_publish(self):
        before=self.balance('a')
        def attempt(n):
            try:return self.batch(ids=self.ids[n:n+3],key='overlap-request-'+str(n))
            except yc.CardError:return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[0,1]))
        self.assertEqual(sum(r is not None for r in results),1);self.assertEqual(self.balance('a'),before-3000)
        with self.connect() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM yc_listings WHERE state='active'").fetchone()[0],3)
    def test_auction_and_destroyed_cards_reject_batch_without_charges(self):
        self.store.create_auction('a',dict(instance_id=self.ids[0],kind='public',pricing='first',clock='fixed',start_price=10,increment=1,duration_hours=1,request_key='protected-batch-auction'))
        before=self.balance('a');self.assertError(lambda:self.batch());self.assertEqual(self.balance('a'),before)
        self.store.destroy_cards('a',dict(instance_ids=[self.ids[1]],confirmed=True))
        self.assertError(lambda:self.batch(ids=self.ids[1:4]));self.assertEqual(self.balance('a'),before)
    def test_same_cid_can_be_shared_without_instance_collision(self):
        original=self.cards[0];other=dict(original);other.update(id=yc.uuid.uuid4().hex,producer='b',owner='b')
        with self.store.transaction() as c:c.execute('INSERT INTO yc_instances VALUES(?,?,?,?,?,?,?,?,?,?,?)',tuple(other.values()))
        first=self.share();second=self.store.share_card('b',dict(instance_id=other['id'],expected_owner='b'))
        self.assertEqual(first['cid'],second['cid']);self.assertNotEqual(first['url'],second['url'])
        self.assertEqual(self.store.public_card(other['id'],other['cid'])['id'],other['id'])
    def test_invalid_public_batch_does_not_publish_valid_subset(self):
        with self.store.transaction() as c:c.execute('UPDATE yc_instances SET owner="b" WHERE id=?',(self.ids[1],))
        self.assertError(lambda:self.store.set_public('a',dict(instance_ids=self.ids[:2])));self.assertEqual(self.public_count(),0)
        self.assertError(lambda:self.store.set_public('a',dict(instance_ids=[self.ids[0]],public='false')))
        self.assertError(lambda:self.batch(ids=['../path']))
