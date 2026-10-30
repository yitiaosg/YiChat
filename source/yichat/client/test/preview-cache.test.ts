import test from 'node:test';import assert from 'node:assert/strict';
import {PreviewCache,previewLifetime,PREVIEW_COUNT,PREVIEW_BYTES,type PreviewEntry,type PreviewStore} from '../src/preview-cache';
function fixture(){let now=100000,calls=0;const rows=new Map<string,PreviewEntry>();const store:PreviewStore={read:async key=>rows.get(key),write:async(key,row)=>{rows.set(key,row)},remove:async key=>{rows.delete(key)}};return {rows,store,clock:()=>now,tick:(n:number)=>{now+=n},load:async()=>{calls++;return {type:'game',data:{height:20}}},calls:()=>calls}}
test('reopening App cache reads persisted preview without another request',async()=>{
 const f=fixture();await new PreviewCache(f.store,f.clock).get('account/device/room/url',f.load);f.tick(86400000);
 assert.equal((await new PreviewCache(f.store,f.clock).get('account/device/room/url',f.load)).type,'game');assert.equal(f.calls(),1);
});
test('scope keys fence account, device, room and URL',async()=>{
 const f=fixture(),cache=new PreviewCache(f.store,f.clock);
 for(const key of ['a/d/r/u','b/d/r/u','a/e/r/u','a/d/s/u','a/d/r/v'])await cache.get(key,f.load);
 assert.equal(f.calls(),5);
});
test('concurrent viewers share one request; manual refresh bypasses cache',async()=>{
 const f=fixture(),c=new PreviewCache(f.store,f.clock);await Promise.all([c.get('k',f.load),c.get('k',f.load)]);assert.equal(f.calls(),1);await c.get('k',f.load,true);assert.equal(f.calls(),2);
});
test('mutable card expiry revalidates and a destroyed card replaces the cached art',async()=>{
 const f=fixture(),c=new PreviewCache(f.store,f.clock);let calls=0;
 const load=async()=>{calls++;return calls===1?{type:'card',data:{name:'card'}}:null};
 await c.get('card',load);f.tick(59000);assert.equal((await c.get('card',load)).type,'card');assert.equal(calls,1);
 f.tick(1001);assert.equal(await c.get('card',load),null);assert.equal(f.rows.get('card')?.value,null);
 assert.equal(await new PreviewCache(f.store,f.clock).get('card',load),null);
});
test('expired or inaccessible preview is never restored from stale disk data',async()=>{
 const f=fixture(),c=new PreviewCache(f.store,f.clock);await c.get('k',f.load);f.tick(31*86400000);
 await assert.rejects(c.get('k',async()=>{throw Object.assign(Error('forbidden'),{status:403})}),/forbidden/);assert.equal(f.rows.has('k'),false);
});
test('storage errors fall back to network and do not fail successful preview',async()=>{
 const bad=async()=>{throw Error('quota')};const c=new PreviewCache({read:bad,write:bad,remove:bad});assert.deepEqual(await c.get('k',async()=>({type:'transaction'})),{type:'transaction'});
});
test('locking while a request is in flight prevents rendering or persisting it',async()=>{
 const f=fixture(),c=new PreviewCache(f.store,f.clock);let release!:(value:any)=>void;const task=c.get('k',()=>new Promise(resolve=>{release=resolve}));
 await new Promise(resolve=>setTimeout(resolve,0));c.close();release({type:'game'});await assert.rejects(task,{name:'AbortError'});assert.equal(f.rows.size,0);
});
test('bounded memory evicts old entries and oversized previews do not flush other entries',async()=>{
 const c=new PreviewCache();let calls=0;const load=async()=>{calls++;return {type:'game'}};
 await c.get('keep',load);await c.get('huge',async()=>({data:'x'.repeat(PREVIEW_BYTES)}));await c.get('keep',load);assert.equal(calls,1);
 for(let i=0;i<PREVIEW_COUNT;i++)await c.get('k'+i,load);await c.get('keep',load);assert.equal(calls,PREVIEW_COUNT+2);
});
test('fixed results and mutable resources have explicit separate freshness bounds',()=>{
 for(const type of ['game','transaction','doudizhu_record'])assert.equal(previewLifetime({type}),30*86400000);
 assert.equal(previewLifetime({type:'card'}),60000);assert.equal(previewLifetime({type:'order'}),5000);assert.equal(previewLifetime({type:'doudizhu'}),20000);assert.equal(previewLifetime(null),20000);
});
