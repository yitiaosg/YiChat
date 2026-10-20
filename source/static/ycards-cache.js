/* Immutable render templates are cached per signed-in identity, never trade state. */
window.createYCardsAssets = function(options) {
  'use strict';
  const {user,api}=options;
  const memory=new Map(),flights=new Map(),queue=[];
  const prefix=(user||'anonymous')+'\0',memoryLimit=32*1024*1024,diskLimit=128*1024*1024;
  let memoryBytes=0,active=0;
  const aborted=()=>new DOMException('加载已取消','AbortError');
  const check=signal=>{if(signal?.aborted)throw aborted()};
  const database=new Promise(resolve=>{
    try {
      const req=indexedDB.open('ycards-render-templates-v1',1);
      req.onupgradeneeded=()=>{const db=req.result;db.createObjectStore('templates');db.createObjectStore('meta')};
      req.onsuccess=()=>resolve(req.result);req.onerror=req.onblocked=()=>resolve(null);
    } catch {resolve(null)}
  });
  async function read(key){
    const db=await database;if(!db)return null;
    return new Promise(resolve=>{try{const tx=db.transaction(['templates','meta'],'readwrite'),r=tx.objectStore('templates').get(key);let value=null;
      r.onsuccess=()=>{value=r.result||null;if(value)tx.objectStore('meta').put({at:Date.now(),size:value.size},key)};
      tx.oncomplete=()=>resolve(value);tx.onabort=tx.onerror=()=>resolve(null);
    }catch{resolve(null)}});
  }
  async function persist(key,record){
    const db=await database;if(!db)return;
    return new Promise(resolve=>{try{const tx=db.transaction(['templates','meta'],'readwrite'),meta=tx.objectStore('meta'),templates=tx.objectStore('templates');
      templates.put(record,key);meta.put({at:Date.now(),size:record.size},key);
      const rows=[],cursor=meta.openCursor();cursor.onsuccess=()=>{const c=cursor.result;if(c){rows.push({key:c.key,...c.value});c.continue();return}
        rows.sort((a,b)=>a.at-b.at);let total=rows.reduce((n,r)=>n+r.size,0),count=rows.length;
        for(const r of rows){if(total<=diskLimit&&count<=80)break;if(r.key===key)continue;templates.delete(r.key);meta.delete(r.key);total-=r.size;count--}
      };tx.oncomplete=tx.onabort=tx.onerror=()=>resolve();
    }catch{resolve()}});
  }
  function remember(key,record){
    if(memory.has(key))memoryBytes-=memory.get(key).size;
    memory.delete(key);memory.set(key,record);memoryBytes+=record.size;
    while(memoryBytes>memoryLimit&&memory.size>1){const first=memory.keys().next().value;memoryBytes-=memory.get(first).size;memory.delete(first)}
    return record.design;
  }
  function pump(){
    while(active<3&&queue.length){const item=queue.shift();if(item.signal.aborted){item.reject(aborted());continue}
      active++;Promise.resolve().then(()=>{check(item.signal);return item.task()}).then(item.resolve,item.reject).finally(()=>{active--;pump()});
    }
  }
  function scheduled(task,signal){return new Promise((resolve,reject)=>{queue.push({task,signal,resolve,reject});pump()})}
  function template(card,signal){
    check(signal);const key=prefix+card.tid;
    if(memory.has(key)){const record=memory.get(key);memory.delete(key);memory.set(key,record);return Promise.resolve(record.design)}
    let f=flights.get(key);
    if(!f){
      f={controller:new AbortController(),users:0,finished:false};flights.set(key,f);
      f.promise=(async()=>{
        const cached=await read(key);check(f.controller.signal);if(cached)return remember(key,cached);
        const query=new URLSearchParams({instance_id:card.id});if(card.render_auction_id)query.set('auction_id',card.render_auction_id);
        const result=await scheduled(()=>api('render-template/'+encodeURIComponent(card.tid)+'?'+query,undefined,{signal:f.controller.signal}),f.controller.signal);
        check(f.controller.signal);const record={design:result.design,size:JSON.stringify(result.design).length*2};
        remember(key,record);await persist(key,record);return record.design;
      })().finally(()=>{f.finished=true;if(flights.get(key)===f)flights.delete(key)});
    }
    f.users++;
    return new Promise((resolve,reject)=>{
      let done=false;
      const release=()=>{if(done)return false;done=true;signal?.removeEventListener('abort',cancel);f.users--;if(!f.users&&!f.finished){f.controller.abort();if(flights.get(key)===f)flights.delete(key)}return true};
      const cancel=()=>{if(release())reject(aborted())};
      signal?.addEventListener('abort',cancel,{once:true});
      f.promise.then(design=>{if(release())resolve(design)},error=>{if(release())reject(error)});
      if(signal?.aborted)cancel();
    });
  }
  async function hydrate(card,signal){check(signal);return {...card,design:card.design||await template(card,signal)}}
  function preload(cards,signal){
    // Fetch all unique templates on this page, with at most three network transfers.
    const seen=new Set();for(const card of cards)if(!seen.has(card.tid)){seen.add(card.tid);hydrate(card,signal).catch(()=>{})}
  }
  function pager(page,total,unit='张成品'){
    const pages=Math.max(1,Math.ceil(total/24));
    return `<button data-page="-1" ${page<=1?'disabled':''}>上一页</button><span>${page} / ${pages} · ${total} ${unit}</span><button data-page="1" ${page>=pages?'disabled':''}>下一页</button><form class="page-jump"><label>跳至 <input name="page" type="number" min="1" max="${pages}" step="1" value="${page}" aria-label="跳转页码"> 页</label><button type="submit">跳转</button></form>`;
  }
  return {hydrate,preload,pager};
};
