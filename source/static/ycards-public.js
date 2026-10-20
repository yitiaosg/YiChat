(() => {
'use strict';
const card=JSON.parse(document.getElementById('public-boot').textContent),status=document.getElementById('public-status'),scope=new AbortController();let renderer;
const api=async(path,data,options={})=>{const r=await fetch('/ycards/api/'+path,{signal:options.signal,cache:'no-store'});const body=await r.json();if(!r.ok)throw Error(body.error||'无法查看卡片');return body};
const assets=createYCardsAssets({user:null,api});
(async()=>{const fresh=await api('public/'+card.id+'/'+encodeURIComponent(card.cid));const data=await assets.hydrate(fresh,scope.signal);if(scope.signal.aborted)return;renderer=new YCardRenderer(document.getElementById('public-card'),data);status.textContent=''})().catch(e=>{if(e.name!=='AbortError')status.textContent=e.message});
window.addEventListener('pagehide',()=>{scope.abort();renderer?.destroy()});
})();
