/* Persist one exact payment request across the top-level YIDT authorization redirect. */
(()=>{'use strict';const original=window.fetch.bind(window),key='yidt.pending';let navigating=false;const incomingIntent=new URLSearchParams(location.search).get('yidt_intent');
window.fetch=async(input,options={})=>{
 const response=await original(input,options);
 if(response.status===402){const d=await response.clone().json();if(d.authorize_url&&!navigating){navigating=true;
  if(!d.binding_required)sessionStorage.setItem(key,JSON.stringify({url:String(input),options:{...options,headers:Object.fromEntries(new Headers(options.headers||{}))},intent:d.intent}));
  location.assign(d.authorize_url);return new Promise(()=>{});}}
 return response;
};
window.addEventListener('DOMContentLoaded',async()=>{
 const params=new URLSearchParams(location.search),id=incomingIntent;
 if(id){const raw=sessionStorage.getItem(key);params.delete('yidt_intent');history.replaceState(null,'',location.pathname+(params.size?'?'+params:'')+location.hash);
  if(raw){const p=JSON.parse(raw);if(p.intent===id){try{p.options.headers['X-YIDT-Intent']=id;const r=await original(p.url,p.options),d=await r.json();sessionStorage.removeItem(key);
    if(!r.ok||d.error)throw Error(d.error||'付款失败');
    if(d.yidt_tx)sessionStorage.setItem('yidt.last',JSON.stringify({hash:d.yidt_tx,sealed:!!d.yidt_private}));
    location.reload();return;
  }catch(e){alert(e.message);}}}
 }
 const service=location.pathname.startsWith('/ycards')?'ycards':location.pathname.startsWith('/games/parachute')?'parachute':null;
 if(service){document.querySelectorAll('a[href="/points/"]').forEach(a=>{a.href='https://yidt.yitiao.org/services/'+service+'/';a.title='管理绑定的 YIDT 钱包';});
  if(service==='ycards'){const box=document.createElement('div');box.style='position:fixed;bottom:12px;right:12px;z-index:12;background:#112340;padding:10px;border-radius:10px';
   const a=document.createElement('a');a.href='https://yidt.yitiao.org/services/ycards/';a.textContent='YIDT 钱包 · 服务管理';box.append(a);const raw=sessionStorage.getItem('yidt.last');if(raw){const last=JSON.parse(raw);if(!last.sealed){const tx=document.createElement('a');tx.href='https://yidt.yitiao.org/explorer/tx/'+last.hash;tx.target='_blank';tx.rel='noopener';tx.textContent='付款详情';tx.style='display:block;font-size:12px';box.append(tx);}}document.body.append(box);
   original('/ycards/api/state').then(r=>r.json()).then(d=>{a.textContent=d.yidt_balance==null?'绑定 YIDT 钱包以继续':d.yidt_balance+' YIDT · 管理';}).catch(()=>{});
  }
 }
});})();
