"""Names, archives and resumable compact saves; only disposable databases."""
import concurrent.futures
import gzip
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from flask import Flask, session
import ycards as yc
import test_ycards as core
from test_ycards import design, layer

class LibraryTest(unittest.TestCase):
    setUp=core.CardsTest.setUp;tearDown=core.CardsTest.tearDown;connect=core.CardsTest.connect
    formal=core.CardsTest.formal;start=core.CardsTest.start;finish=core.CardsTest.finish
    instances=core.CardsTest.instances;balance=core.CardsTest.balance;assertError=core.CardsTest.assertError

    def client(self):
        app=Flask(__name__,template_folder=str(Path(yc.__file__).parent/'templates'));app.secret_key='test';app.config['TESTING']=True
        yc.init_app(app,lambda:dict(username=session['user'],role='user') if 'user' in session else None,lambda u:None,self.store,worker=False)
        client=app.test_client()
        with client.session_transaction() as s:s['user']='a';s['yc_csrf']='csrf'
        return client

    def test_template_instance_names_independent_and_transfer(self):
        self.formal();self.start();self.finish();iid=self.instances()[0]['id'];original=(self.store.cards/'T.card').read_bytes()
        self.store.rename('a',dict(tid='T',name='新模板'))
        self.assertEqual(self.store.design('a',tid='T')['design']['name'],'新模板')
        self.assertEqual(self.store.preview(iid,'a')['name'],'测试')
        self.assertEqual(original,(self.store.cards/'T.card').read_bytes())
        self.store.rename('a',dict(instance_id=iid,name='独立卡名'))
        self.start();self.finish();cards=self.store.cards_list('a',False,{})['items']
        self.assertEqual({c['name'] for c in cards},{'独立卡名','新模板'})
        lid=self.store.list_card('a',dict(instance_id=iid,price=100))['id']
        self.store.rename('a',dict(instance_id=iid,name='挂售改名'))
        self.assertEqual(self.store.cards_list(None,True,{'name':'挂售改名'})['total'],1)
        self.assertEqual(self.store.history('a')['active'][0]['name'],'挂售改名')
        self.store.buy('b',dict(listing_id=lid,expected_price_cents=10000))
        self.assertError(lambda:self.store.rename('a',dict(instance_id=iid,name='越权')))
        self.store.rename('b',dict(instance_id=iid,name='买家命名'))
        self.assertEqual(self.store.history('a')['sold'][0]['name'],'买家命名')
        self.assertEqual(original,(self.store.cards/'T.card').read_bytes())

    def test_rename_auction_showcase_public_and_reject_invalid(self):
        self.formal();self.start();self.finish();iid=self.instances()[0]['id']
        auction=self.store.create_auction('a',dict(instance_id=iid,kind='public',pricing='first',clock='fixed',start_price=100,increment=10,duration_hours=24,request_key='library-auction-1234'))
        self.store.set_showcase('a',dict(slot=1,instance_id=iid))
        self.store.rename('a',dict(instance_id=iid,name='拍卖改名'))
        self.assertEqual(self.store.showcase('a')['cards'][0]['name'],'拍卖改名')
        self.store.set_public('a',dict(instance_ids=[iid]))
        self.assertEqual(self.store.public_card(iid,self.instances()[0]['cid'])['name'],'拍卖改名')
        self.assertEqual(self.store.auction_detail(auction['id'],'b')['name'],'拍卖改名')
        for target in [dict(tid='T'),dict(instance_id=iid)]:
            self.assertError(lambda:self.store.rename('b',dict(target,name='外人')))
            for value in ['',None,42,'字'*21]:self.assertError(lambda:self.store.rename('a',dict(target,name=value)))
        self.assertError(lambda:self.store.rename('a',dict(tid='T',instance_id=iid,name='歧义')))

    def test_archive_restore_and_running_production(self):
        self.formal();self.start();self.store.archive('a',dict(tid='T',archived=True));self.finish()
        self.assertEqual(len(self.instances()),1)
        self.assertEqual(self.store.templates('a',summary=True,production=True),[])
        self.assertTrue(self.store.templates('a',summary=True)[0]['archived'])
        self.assertEqual(self.store.design('a',tid='T')['design']['tid'],'T')
        self.assertError(lambda:self.start())
        self.assertError(lambda:self.store.delete('a',dict(tid='T',confirmed=True)))
        self.assertError(lambda:self.store.archive('b',dict(tid='T',archived=False)))
        self.store.archive('a',dict(tid='T',archived=False));q=self.store.quote('a',dict(tid='T',mode='personal'))
        self.store.archive('a',dict(tid='T',archived=True));before=self.balance('a')
        self.assertError(lambda:self.store.start('a',dict(tid='T',mode='personal',count=1,improvement=0,quote_token=q['token'],request_key='archived-quote-1234')))
        self.assertEqual(before,self.balance('a'));self.store.archive('a',dict(tid='T',archived=False));self.start();self.finish()
        self.assertEqual(len(self.instances()),2)

    def test_draft_rename_revision_archive_and_summary_no_blob_parse(self):
        saved=self.store.save('a',dict(design=design()),False);did=saved['draft_id']
        self.store.rename('a',dict(draft_id=did,name='草稿改名'));self.store.archive('a',dict(draft_id=did,archived=True))
        self.assertError(lambda:self.store.save('a',dict(draft_id=did,revision=1,design=design()),False))
        self.assertError(lambda:self.store.delete('a',dict(draft_id=did,confirmed=True)))
        self.assertEqual(json.loads((self.store.cards/f'draft_{did}.card').read_text())['name'],'草稿改名')
        self.formal()
        with patch.object(yc.json,'loads',side_effect=AssertionError('Summary parsed full design')):
            summary=self.store.templates('a',summary=True)
        self.assertEqual(len(summary),2);self.assertTrue(next(x for x in summary if x.get('draft_id'))['archived'])
        self.store.archive('a',dict(draft_id=did,archived=False));self.assertFalse(self.store.design('a',draft_id=did).get('archived',False))

    def test_migration_preserves_names_and_archived_templates(self):
        self.formal();self.start();self.finish();iid=self.instances()[0]['id']
        self.store.rename('a',dict(instance_id=iid,name='持久名称'));self.store.archive('a',dict(tid='T',archived=True))
        with self.store.transaction() as c:
            c.execute('UPDATE yc_templates SET material=NULL')
        self.store=yc.Store(self.connect,self.store.cards)
        self.assertEqual(self.store.preview(iid,'a')['name'],'持久名称')
        self.assertEqual(self.store.templates('a',summary=True)[0]['material'],'plastic')
        self.assertTrue(self.store.templates('a',summary=True)[0]['archived'])

    def test_legacy_schema_upgrade_and_name_backfill(self):
        self.formal();self.start();self.finish();self.store.save('a',dict(design=design()),False)
        iid=self.instances()[0]['id']
        with self.store.transaction() as c:
            c.execute('DROP TABLE yc_card_names');c.execute('DROP TABLE yc_save_requests')
            c.execute('ALTER TABLE yc_templates DROP COLUMN material')
            c.execute('UPDATE yc_templates SET deleted=1')
            for column in ['name','material','cost','archived']:c.execute('ALTER TABLE yc_drafts DROP COLUMN '+column)
        self.store=yc.Store(self.connect,self.store.cards)
        self.assertEqual(self.store.preview(iid,'a')['name'],'测试')
        rows=self.store.templates('a',summary=True);self.assertEqual(len(rows),2)
        self.assertTrue(next(r for r in rows if r.get('tid'))['archived'])
        self.assertFalse(next(r for r in rows if r.get('draft_id'))['archived'])
        self.store.archive('a',dict(tid='T',archived=False));self.start();self.finish()
        self.assertEqual(len(self.instances()),2)

    def test_compact_gzip_save_and_private_status(self):
        client=self.client();payload=dict(tid='Packed',design=design(),confirmed=True,request_key='packed-request-12345')
        headers={'Content-Type':'application/json','Content-Encoding':'gzip','X-YCards-CSRF':'csrf'}
        body=gzip.compress(json.dumps(payload).encode());r=client.post('/ycards/api/templates?compact=1',data=body,headers=headers)
        self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.json,dict(tid='Packed'));self.assertLess(len(r.data),100)
        self.assertIn('ycards-save;dur=',r.headers['Server-Timing'])
        self.assertEqual(client.post('/ycards/api/templates?compact=1',data=body,headers=headers).json,r.json)
        self.assertEqual(client.get('/ycards/api/save-status/packed-request-12345').json,dict(saved=True,result=r.json))
        with client.session_transaction() as s:s['user']='b'
        self.assertFalse(client.get('/ycards/api/save-status/packed-request-12345').json['saved'])
        self.assertEqual(client.post('/ycards/api/drafts',data=b'bad',headers=headers).status_code,400)
        with patch.object(yc,'MAX_BODY',1024):
            self.assertEqual(client.post('/ycards/api/drafts',data=gzip.compress(b' '*2048),headers=headers).status_code,413)
        self.assertEqual(client.post('/ycards/api/drafts',data=body[:-2],headers=headers).status_code,400)

    def test_concurrent_save_retry_no_duplicate_draft_or_revision(self):
        data=dict(design=design(),request_key='concurrent-save-123')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.store.save('a',data,False,compact=True),range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(len(self.store.templates('a')),1)
        self.assertError(lambda:self.store.save('a',dict(data,design=dict(design(),name='不同')),False,compact=True))
        saved=results[0];update=dict(data,draft_id=saved['draft_id'],revision=1,request_key='update-save-12345')
        a=self.store.save('a',update,False,compact=True);b=self.store.save('a',update,False,compact=True)
        self.assertEqual(a,b);self.assertEqual(a['revision'],2)

    def test_assets_checked_once_and_targeted_publication(self):
        d=design();d['layers']=[layer(id='one'),layer(id='two')]
        with patch.object(yc,'asset',wraps=yc.asset) as validate:
            yc.validate_design(d)
            image_calls=[c for c in validate.call_args_list if c.args and c.args[0]]
            self.assertEqual(len(image_calls),1)
        first=self.store.save('a',dict(design=design()),False);p=self.store.cards/f"draft_{first['draft_id']}.card";before=p.stat().st_mtime_ns
        self.formal();self.assertEqual(p.stat().st_mtime_ns,before)
        self.assertEqual(len((self.store.cards/'T.card').read_bytes())>0,True)

if __name__=='__main__':unittest.main()
