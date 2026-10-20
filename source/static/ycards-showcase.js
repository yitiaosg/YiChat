(() => {
'use strict';
const boot=JSON.parse(document.getElementById('yc-showcase-boot').textContent),root=document.getElementById('yc-showcase');
const $=s=>root.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state=boot.state,displayRenderers=[],pickerRenderers=[],displayScope,pickerScope,pickerPage=1,pickerSlot=1,purchaseKey=null,purchaseExpected=null;
const message=s=>$('#yc-showcase-message').textContent=s;
async function api(path,data,options={}){
 const r=await fetch('/ycards/api/'+path,{method:data===undefined?'GET':'POST',signal:options.signal,headers:data===undefined?{}:{'Content-Type':'application/json','X-YCards-CSRF':boot.csrf},body:data===undefined?undefined:JSON.stringify(data)});
 const value=await r.json();if(!r.ok)throw Error(value.error||'操作失败');return value;
}
const assets=createYCardsAssets({user:boot.viewer,api});
function stopPicker(){pickerScope?.abort();pickerRenderers.forEach(r=>r.destroy());pickerRenderers=[]}
function closePicker(){stopPicker();$('#yc-card-picker').close()}
async function render(){
 displayScope?.abort();const scope=displayScope=new AbortController();displayRenderers.forEach(r=>r.destroy());displayRenderers=[];
 const bySlot=new Map(state.cards.map(c=>[c.slot,c]));
 $('#yc-showcase-grid').innerHTML=Array.from({length:state.slots},(_,i)=>{const c=bySlot.get(i+1);if(!boot.edit&&!c)return '';return `<article class="yc-display-slot"><div class="card-stage ${c?'':'yc-empty-slot'}" data-slot="${i+1}">${c?'':'空展示框'}</div>${boot.edit?`<div class="yc-display-name">${esc(c?.name||`展示框 ${i+1}`)}</div>`:''}${boot.edit?`<div class="yc-slot-actions"><button type="button" data-choose="${i+1}">${c?'更换':'选择卡片'}</button>${c?`<button type="button" data-remove="${i+1}">取消展示</button>`:''}</div>`:''}</article>`}).join('');
 if(boot.edit){$('#yc-showcase-count').textContent=`${state.cards.length} / ${state.slots} 已展示`;$('#yc-buy-slot').hidden=state.slots>=6;$('#yc-showcase-balance').textContent=`余额 ${(state.balance_cents/100).toLocaleString()} YIDT${state.slots>=6?' · 展示框已达上限':''}`}
 assets.preload(state.cards,scope.signal);
 await Promise.all(state.cards.map(async card=>{try{const data=await assets.hydrate(card,scope.signal);if(!scope.signal.aborted)displayRenderers.push(new YCardRenderer($(`[data-slot="${card.slot}"]`),data))}catch(e){if(e.name!=='AbortError')message('卡片预览加载失败，请刷新重试')}}));
}
async function refresh(){state=await api('showcase');await render()}
async function loadPicker(){
 stopPicker();const scope=pickerScope=new AbortController();$('#yc-picker-cards').replaceChildren();$('#yc-picker-error').textContent='';
 const query=new URLSearchParams(new FormData($('#yc-picker-filter')));query.set('page',pickerPage);query.set('compact','1');
 try{
  const result=await api('mine?'+query,undefined,{signal:scope.signal});if(scope.signal.aborted)return;pickerPage=result.page;
  $('#yc-picker-cards').innerHTML=result.items.length?result.items.map(c=>`<article class="yc-picker-item"><div class="card-stage" data-pick-preview="${c.id}"></div><div class="yc-display-name">${esc(c.name)}</div><button type="button" data-pick="${c.id}" ${c.showcased?'disabled':''}>${c.showcased?'已在展示':'展示此卡片'}</button></article>`).join(''):'<p>没有符合条件的仓库卡片。</p>';
  $('#yc-picker-pages').innerHTML=assets.pager(pickerPage,result.total);assets.preload(result.items,scope.signal);
  await Promise.all(result.items.map(async card=>{try{const data=await assets.hydrate(card,scope.signal);if(!scope.signal.aborted)pickerRenderers.push(new YCardRenderer($(`[data-pick-preview="${card.id}"]`),data))}catch(e){if(e.name!=='AbortError')$('#yc-picker-error').textContent='部分预览加载失败；可重试筛选。'}}));
 }catch(e){if(e.name!=='AbortError')$('#yc-picker-error').textContent=e.message}
}
if(boot.edit){
 $('#yc-showcase-grid').addEventListener('click',async e=>{
  const button=e.target.closest('button');if(!button)return;
  if(button.dataset.choose){pickerSlot=Number(button.dataset.choose);pickerPage=1;$('#yc-picker-title').textContent=`展示框 ${pickerSlot} · 选择仓库卡片`;$('#yc-card-picker').showModal();await loadPicker()}
  else if(button.dataset.remove){button.disabled=true;try{await api('showcase',{slot:Number(button.dataset.remove),instance_id:null});message('已取消展示');await refresh()}catch(e){message(e.message);button.disabled=false}}
 });
 $('#yc-close-picker').onclick=closePicker;$('#yc-card-picker').addEventListener('cancel',e=>{e.preventDefault();closePicker()});
 $('#yc-picker-filter').onsubmit=e=>{e.preventDefault();pickerPage=1;loadPicker()};
 $('#yc-picker-pages').onclick=e=>{const b=e.target.closest('[data-page]');if(b){pickerPage+=Number(b.dataset.page);loadPicker()}};
 $('#yc-picker-pages').onsubmit=e=>{e.preventDefault();if(!e.target.reportValidity())return;pickerPage=Number(new FormData(e.target).get('page'));loadPicker()};
 $('#yc-picker-cards').onclick=async e=>{const b=e.target.closest('[data-pick]');if(!b)return;b.disabled=true;try{await api('showcase',{slot:pickerSlot,instance_id:b.dataset.pick});closePicker();message('已更新主页展示');await refresh()}catch(e){$('#yc-picker-error').textContent=e.message;b.disabled=false}};
 $('#yc-buy-slot').onclick=async()=>{
  if(!confirm('确认支付 10,000 YIDT 购买 1 个永久展示框？最多可拥有 6 个。'))return;
  const b=$('#yc-buy-slot');b.disabled=true;
  if(purchaseExpected!==state.slots){purchaseExpected=state.slots;purchaseKey=crypto.randomUUID()}
  try{await api('showcase/purchase',{expected_slots:purchaseExpected,request_key:purchaseKey});message('展示框购买成功');await refresh()}catch(e){message(e.message)}finally{b.disabled=false}
 };
}
render().catch(e=>message(e.message));
window.addEventListener('pagehide',()=>{displayScope?.abort();stopPicker();displayRenderers.forEach(r=>r.destroy())});
})();
