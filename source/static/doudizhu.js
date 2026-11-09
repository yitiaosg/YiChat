(()=>{'use strict';
const $=id=>document.getElementById(id),prefix='/games/doudizhu',user=document.body.dataset.user,csrf=document.body.dataset.csrf;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const modeLabel=v=>({normal:'普通 · 单副牌',happy:'欢乐 · 不洗牌',king:'成王败寇 · 双副牌',double:'双副牌 · 旧规则'}[v]||'普通 · 单副牌');
const fmt=n=>{const a=Math.abs(n),unit=a>1000000?'B':a>1000?'K':'';return unit?(n/(unit==='B'?1000000:1000)).toFixed(2).replace(/\.?0+$/,'')+unit:Number(n).toLocaleString('zh-CN')};
let state=null,variant=DDZTheme.variant,base=1,price=328,busy=false,selected=new Set(),lastHand='',lastActions='',lastRevision='',hints=[],hintIndex=0,clockOffset=0,toastTimer,drag=null;
const effects=DDZEffects.create({onResult:()=>{const button=$('see-result');if(button){button.disabled=false;button.textContent='查看结算'}showResult()}});
const key=()=>crypto.randomUUID().replaceAll('-','');
function operationKey(kind,value){const name='ddz.operation.v2.'+kind;let old;try{old=JSON.parse(sessionStorage.getItem(name))}catch{}if(!old||old.value!==value){old={value,key:key()};sessionStorage.setItem(name,JSON.stringify(old))}return old.key}
const defaultTiers=[1,2,3,5,10,20,100].map(n=>({base:n,minimum:n*2000,fee:n*100-7}));
const defaultPackages=[38,68,128,328,648,1268,3468,6488].map((yidt,i)=>({yidt,beans:[880,1880,3680,10580,22280,52780,157580,318880][i],counter_cards:[0,0,1,4,10,30,100,256][i],super_cards:[0,0,0,2,5,15,50,128][i]}));
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500)}
function requireLogin(){if(user)return true;location.assign('/login/?next='+encodeURIComponent(location.pathname));return false}
async function api(path,payload){const options={cache:'no-store'};if(payload!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-DDZ-CSRF':csrf};options.body=JSON.stringify(payload)}const r=await fetch(prefix+'/api/'+path,options);const d=await r.json();if(!r.ok||d.error)throw Error(d.error||'操作失败，请重试');return d}
async function mutation(path,payload){if(busy||!requireLogin())return;busy=true;document.body.classList.add('busy');try{const d=await api(path,payload);if(d.room===null||d.room)render(d);return d}catch(e){toast(e.message);await load()}finally{busy=false;document.body.classList.remove('busy')}}
async function load(){if(!user)return;try{const d=await api('state');render(d)}catch(e){toast(e.message)}}
function card(id,small=false,selectable=false){if(id===null)return '<span class="card back small">♠</span>';const face=id%54,joker=face>=52,r=joker?face-36:Math.floor(face/4)+3,suit=['♠','♥','♣','♦'][face%4],red=joker?face===53:face%4===1||face%4===3,label=joker?(face===53?'大王':'小王'):({11:'J',12:'Q',13:'K',14:'A',15:'2'}[r]||r),classes='card'+(small?' small':'')+(red?' red':'')+(selected.has(id)&&selectable?' selected':'');const content=joker?'<span class="joker">JOKER</span>':'<span class="rank'+(r===10?' ten':'')+'">'+label+'</span><span class="suit">'+suit+'</span><span class="card-center">'+suit+'</span>';return selectable?'<button class="'+classes+'" type="button" data-card="'+id+'" aria-label="'+(joker?label:suit+label)+'" aria-pressed="'+selected.has(id)+'">'+content+'</button>':'<span class="'+classes+'" aria-label="'+(joker?label:suit+label)+'">'+content+'</span>'}
function tiers(){
 const list=state?.tiers||defaultTiers,allIn=variant==='king',t=list.find(t=>t.base===(allIn?1:base));
 $('tiers').hidden=allIn;$('king-rules').hidden=!allIn;
 $('tier-heading').textContent=allIn?'成王败寇，胜者全收':'选个倍率，开始一局';
 $('tiers').innerHTML=list.map(t=>'<button class="tier '+(t.base===base?'active':'')+'" data-base="'+t.base+'" aria-pressed="'+(t.base===base)+'"><strong>'+t.base+'<small>倍</small></strong><span>'+fmt(t.minimum)+' 豆起</span></button>').join('');
 $('tier-minimum').textContent=fmt(t.minimum);$('tier-fee').textContent=fmt(t.fee);
}

function packages(){
 $('packages').innerHTML=(state?.packages||defaultPackages).map(p=>'<button class="package '+(p.yidt===price?'active':'')+'" data-price="'+p.yidt+'"><b>'+fmt(p.beans)+'</b> 豆<span>'+Number(p.yidt).toLocaleString('zh-CN')+' YIDT</span><small class="package-gift">记牌器 '+(p.counter_cards||0)+' · 超级加倍卡 '+(p.super_cards||0)+'</small></button>').join('');
}
function counterOption(){return $('use-counter').checked}
function syncCounterChoice(room){
 const balance=state?.counter_cards||0,waiting=room?.status==='waiting';$('counter-inventory').textContent=balance;
 if(!balance)$('use-counter').checked=false;$('use-counter').disabled=!balance;
 $('room-counter-choice').hidden=!waiting;$('room-use-counter').checked=!!room?.use_counter;$('room-use-counter').disabled=!balance;
 $('room-counter-inventory').textContent=balance;
}

function profile(p,self=false){if(!p)return '<div class="empty-seat">＋</div><p class="player-name muted">等待牌友</p>';const role=p.role==='地主',img=role?'landlord-128.webp':'farmer.svg';const avatar=p.avatar?'<img class="user-avatar" src="'+esc(p.avatar)+'" alt="'+esc(p.nickname)+'的头像">':'<span class="user-avatar">'+esc([...p.nickname][0]||'友')+'</span>';return '<div class="player-portrait"><img class="role-image" src="/static/doudizhu/'+img+'" alt="'+esc(p.role)+'角色">'+avatar+'</div><div><p class="player-name"><b>'+esc(p.nickname)+'</b><span class="role-badge '+(role?'lord':'')+'">'+esc(p.role)+'</span></p><span class="player-beans">'+fmt(p.beans)+' 豆</span>'+(self?'':'<br><span class="card-count">余 '+p.count+' 张</span>')+'<p class="speech">'+esc(['bidding','doubling'].includes(state?.room?.status)||['不出','不要'].includes(p.speech)?'':p.speech)+'</p></div>'}
function render(d){if(d.room&&state?.room&&d.room.share_url===state.room.share_url&&d.room.revision<state.room.revision)return;state=d;for(const kind of ['recharge','redeem','item_counter','item_super']){try{const name='ddz.operation.v2.'+kind,old=JSON.parse(sessionStorage.getItem(name));if(old&&d.exchange_keys?.includes(old.key))sessionStorage.removeItem(name)}catch{}}$('bean-balance').textContent=fmt(d.beans);$('super-card-balance').textContent=d.super_cards||0;$('counter-card-balance').textContent=d.counter_cards||0;tiers();packages();$('wallet-status').textContent=d.wallet?d.wallet_balance+' YIDT · '+d.wallet.slice(0,9)+'…':'尚未绑定斗地主钱包';$('bean-history').innerHTML=d.history.length?d.history.map(h=>'<div class="history-row"><span>'+esc(h.reason)+'<small>'+new Date(h.created*1000).toLocaleString('zh-CN')+'</small></span><span class="'+(h.delta>=0?'positive':'negative')+'">'+(h.delta>0?'+':'')+fmt(h.delta)+'</span></div>').join(''):'<p>暂无豆子记录</p>';previewRedeem();const r=d.room;syncCounterChoice(r);DDZCounter.render($('card-counter'),r);DDZTheme.set(r?.variant||variant);$('lobby').hidden=!!r;$('room').hidden=!r;$('room').classList.toggle('double-deck',['king','double'].includes(r?.variant));if(!r){$('room-counter-choice').hidden=true;effects.sync(null);cancelDrag();selected.clear();lastHand='';lastActions='';return}if(r.status==='closed'){effects.sync(null);mutation('leave',{});return}
if(lastRevision!==r.code+':'+r.revision){lastRevision=r.code+':'+r.revision;hints=[];hintIndex=0}clockOffset=r.server_now*1000-Date.now();$('copy-code').textContent=r.code;$('share-room').hidden=r.mode!=='private';$('share-room').dataset.yichatUrl=r.mode==='private'?(r.share_url||''):'';$('room-mode').textContent=modeLabel(r.variant)+' / '+(r.mode==='match'?'匹配场':'好友房');$('room-stakes').hidden=r.variant==='king';$('room-base').textContent=r.base;const lordDouble=r.lord==null?1:r.players[r.lord].double||1,personal=r.me===r.lord?1:r.players[r.me].double||1;$('room-multiplier').textContent=fmt(r.base*r.multiplier*lordDouble*personal);$('bottom').innerHTML=r.bottom.map(c=>card(c,true)).join('');$('bottom-label').textContent=r.lord==null?'地主底牌 · 待揭晓':'地主底牌';const left=(r.me+1)%3,right=(r.me+2)%3;$('player-left').innerHTML=profile(r.players[left]);$('player-right').innerHTML=profile(r.players[right]);$('player-self').innerHTML=profile(r.players[r.me],true);const me=r.players[r.me];if(me)$('account-name').title=me.nickname;
for(const [name,i] of [['left',left],['right',right],['self',r.me]]){const el=$('played-'+name),passed=(r.passed||[]).includes(i)||['不出','不要'].includes(r.players[i]?.speech),speech=['bidding','doubling'].includes(r.status)?r.players[i]?.speech:'',html=speech?'<strong class="pass-mark phase-mark">'+esc(speech)+'</strong>':passed?'<strong class="pass-mark">不要</strong>':(r.table?.[String(i)]||[]).map(c=>card(c)).join('');if(el.innerHTML!==html){el.innerHTML=html;el.classList.remove('deal-in');void el.offsetWidth;if(html)el.classList.add('deal-in')}}
$('notice').textContent=r.notice;$('waiting-symbol').hidden=r.status!=='waiting';$('waiting-symbol').classList.toggle('matching',r.mode==='match');$('matching-count').hidden=r.status!=='waiting';$('waiting-actions').innerHTML=r.status==='waiting'&&r.mode==='private'&&r.is_owner?'<button id="start-game" class="primary" '+(r.players.length<3?'disabled':'')+'>'+(r.players.length<3?'等待三人到齐':'开始游戏 →')+'</button>':'';$('leave').disabled=!['waiting','finished','closed'].includes(r.status);$('leave').textContent=r.status==='waiting'&&r.mode==='match'?'取消匹配':'退出房间 ↗';
const signature=r.code+':'+JSON.stringify(me.hand);if(lastHand!==signature){cancelDrag();lastHand=signature;selected=new Set([...selected].filter(c=>me.hand.includes(c)));$('hand').innerHTML=Array.from({length:Math.ceil(me.hand.length/10)},(_,i)=>me.hand.slice(i*10,i*10+10)).map(row=>'<div class="hand-row">'+row.map(c=>card(c,false,true)).join('')+'</div>').join('');hints=[];hintIndex=0}
const canPlay=r.status==='playing'&&r.turn===r.me,canBid=r.status==='bidding'&&r.turn===r.me,canDouble=r.status==='doubling'&&me.double===null;let buttons='';if(canBid)buttons='<button class="secondary" data-bid="false">'+(r.calling?'不叫':'不抢')+'</button><button class="primary" data-bid="true">'+(r.calling?'叫地主':r.variant==='king'?'抢地主':'抢地主 ×2')+'</button>';else if(canDouble)buttons='<button class="secondary" data-double="false">不加倍</button><button class="primary" data-double="true">加倍 ×2</button><button id="super-double" class="primary super-button" '+(d.super_cards>0?'':'disabled')+' title="消耗 1 张超级加倍卡">超级加倍 ×4 <small>余 '+(d.super_cards||0)+' 张</small></button>';else if(canPlay)buttons='<button id="pass" class="secondary" '+(r.last_shape?'':'disabled')+'>不要</button><button id="hint" class="secondary">提示</button><button id="play" class="primary">出牌 →</button>';else if(r.status==='finished')buttons='<button id="see-result" class="primary">查看结算</button><button id="share-finished" class="secondary">分享本局</button>';else if(r.status!=='waiting')buttons='<span class="muted">'+(r.status==='doubling'?'等待其他玩家选择加倍':'等待 '+esc(r.players[r.turn]?.nickname||'牌友')+' 操作')+'</span>';if(lastActions!==buttons){lastActions=buttons;$('actions').innerHTML=buttons}
$('hand-help').textContent=r.status==='waiting'?'分享房间码，邀请好友一起玩':r.status==='finished'?'本局已结算，点击查看结果':canPlay?'滑动选牌 / 取消 · 空选时点牌智能跟牌':'正在对局 · 超时自动托管';updateTimer();$('remaining-hands').hidden=r.status!=='finished';if(r.status==='finished')$('remaining-players').innerHTML=remainingHands(r);effects.sync(r);if(effects.resultPending&&$('see-result')){$('see-result').disabled=true;$('see-result').textContent='结算动画…'}}
function updateTimer(){const r=state?.room;if(!r)return;const waiting=r.status==='waiting',elapsed=Math.max(0,Math.floor((Date.now()+clockOffset)/1000-r.created));if(waiting)$('matching-count').textContent=r.mode==='match'?(elapsed>=10?'已匹配 '+r.players.length+' / 3 人 · 等待 '+elapsed+' 秒':'正在寻找牌友 · '+elapsed+' 秒'):r.players.length+' / 3 人 · 房间码 '+r.code;const timer=['playing','bidding','doubling'].includes(r.status);$('turn-indicator').hidden=!timer;if(timer){$('timer').textContent=Math.max(0,Math.ceil(r.deadline-(Date.now()+clockOffset)/1000));$('turn-label').textContent=r.status==='doubling'?'加倍倒计时':r.turn===r.me?'轮到你了':'等待牌友'}}
function remainingHands(r){return r.players.map((p,i)=>'<section class="remaining-player"><p>'+esc(p.nickname)+(i===r.me?'（你）':'')+' · '+p.role+' · 剩 '+p.hand.length+' 张</p><div class="remaining-cards">'+(p.hand.length?p.hand.map(c=>card(c,true)).join(''):'<span class="muted">已出完</span>')+'</div></section>').join('')}
function showResult(){const r=state?.room;if(!r||r.status!=='finished')return;const won=(r.winner===r.lord)===(r.me===r.lord);$('result-title').textContent=won?'赢得漂亮':'再来一手';$('result-note').textContent=(r.winner===r.lord?'地主获胜':'农民获胜')+(r.spring?' · '+(r.winner===r.lord?'春天':'反春天')+(r.variant==='king'?'':' ×2'):'')+(r.variant==='king'?' · 成王败寇 · 全额结算':' · 公共倍率 '+r.base*r.multiplier+'×');$('result-players').innerHTML=r.players.map((p,i)=>'<div class="result-row '+(i===r.me?'me':'')+'"><span class="role-badge '+(i===r.lord?'lord':'')+'">'+p.role+'</span><span>'+esc(p.nickname)+(i===r.me?'（你）':'')+'</span><span class="'+(r.deltas[i]<0?'negative':'')+'">'+(r.deltas[i]>0?'+':'')+fmt(r.deltas[i])+'</span></div>').join('');$('result-hands').innerHTML=remainingHands(r);$('result-fee').textContent='入场费已在开局扣除：每人 '+fmt(r.tier.fee)+' 豆。上方为牌局输赢。';if(!$('result-dialog').open)$('result-dialog').showModal()}
async function shareFinished(){
 const r=state?.room;if(!r||r.status!=='finished'||busy)return;
 const id=r.id||r.share_url?.match(/\/([a-f0-9]{32})\/$/)?.[1];
 if(!id)return toast('没有找到本局记录，请刷新后重试');
 const buttons=[$('share-finished'),$('result-share')].filter(Boolean);
 buttons.forEach(b=>b.disabled=true);
 try{const result=await mutation('records/'+id+'/share',{});if(result)location.assign(result.options_url)}
 finally{buttons.forEach(b=>b.disabled=false)}
}

function previewRedeem(){const n=Number($('redeem-beans').value),valid=Number.isSafeInteger(n)&&n>0,amount=valid?Math.max(0,n*.02-Number(state?.gas||.00390625)).toFixed(8).replace(/0+$/,'').replace(/\.$/,''):null;$('redeem-preview').textContent=valid?'预计到账 '+amount+' YIDT · gas '+(state?.gas||'0.00390625')+' YIDT':'实际到账 = 豆子 × 0.02 − gas';$('redeem').disabled=!valid||!state?.wallet||state?.held||n>state?.beans;$('redeem-all').disabled=!state||state.held}
async function act(payload){const r=state?.room;if(!r)return;await mutation('action',{...payload,revision:r.revision})}
$('tiers').addEventListener('click',e=>{const b=e.target.closest('[data-base]');if(b){base=Number(b.dataset.base);tiers()}});$('packages').addEventListener('click',e=>{const b=e.target.closest('[data-price]');if(b){price=Number(b.dataset.price);packages()}});
$('rules-open').onclick=()=>$('rules-dialog').showModal();$('wallet-open').onclick=()=>{if(requireLogin())$('wallet-dialog').showModal()};$('join-open').onclick=()=>{if(requireLogin()){$('join-dialog').showModal();$('room-code').focus()}};document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());document.querySelectorAll('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close()}}));
$('match').onclick=()=>mutation('rooms',{mode:'match',base,variant,rules_version:3,use_counter:counterOption()});$('create').onclick=()=>mutation('rooms',{mode:'create',base,variant,rules_version:3,use_counter:counterOption()});$('leave').onclick=()=>mutation('leave',{});$('join-form').onsubmit=async e=>{e.preventDefault();const d=await mutation('rooms',{mode:'join',rules_version:3,use_counter:counterOption(),code:$('room-code').value.trim()});if(d)$('join-dialog').close()};$('copy-code').onclick=async()=>{try{await navigator.clipboard.writeText(state.room.code);toast('房间码已复制：'+state.room.code)}catch{toast('房间码：'+state.room.code)}};
$('waiting-actions').addEventListener('click',e=>{if(e.target.closest('#start-game'))act({action:'start'})});$('actions').addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.bid!==undefined)return act({action:'bid',yes:b.dataset.bid==='true'});if(b.dataset.double!==undefined)return act({action:'double',yes:b.dataset.double==='true'});if(b.id==='super-double')return act({action:'super_double'});if(b.id==='pass')return act({action:'play',cards:[]});if(b.id==='play'){if(!selected.size)return toast('请先选择要出的牌');return act({action:'play',cards:DDZCards.order([...selected],DDZCards.classify([...selected],state.room.last_shape))})}if(b.id==='share-finished')return shareFinished();if(b.id==='see-result'){if(!effects.resultPending)showResult();return}if(b.id==='hint'){try{if(!hints.length){const r=state.room;hints=DDZCards.suggestions(r.players[r.me].hand,r.last_shape);hintIndex=0}if(!hints.length)return toast('没有能压过上家的牌，可以选择不要');selected=new Set(hints[hintIndex++%hints.length]);syncSelected()}catch(err){toast(err.message)}}});
function syncSelected(){document.querySelectorAll('#hand [data-card]').forEach(b=>{const yes=selected.has(Number(b.dataset.card));b.classList.toggle('selected',yes);b.setAttribute('aria-pressed',String(yes))})}
function tapCard(id){
 const r=state?.room;if(!r||!r.players[r.me].hand.includes(id))return;
 if(selected.has(id))selected.delete(id);
 else if(!selected.size&&r.status==='playing'&&r.last_shape){
  const choices=DDZCards.suggestions(r.players[r.me].hand,r.last_shape,id);
  selected=new Set(choices[0]||[id]);
 }else selected.add(id);
 syncSelected();
}
function cancelDrag(){
 if(!drag)return;const old=drag;drag=null;selected=old.before;
 if($('hand').hasPointerCapture(old.id))$('hand').releasePointerCapture(old.id);
 syncSelected();
}
// Subtract higher cards, including raised cards, from each visible hit region.
// Freeze regions for a gesture so highlighting cannot move the finger targets.
function handRegions(){
 return [...$('hand').querySelectorAll('.hand-row')].flatMap(row=>{
  const buttons=[...row.querySelectorAll('[data-card]')],rects=buttons.map(el=>el.getBoundingClientRect());
  return buttons.flatMap((el,i)=>{
   let pieces=[{left:rects[i].left,right:rects[i].right,top:rects[i].top,bottom:rects[i].bottom}];
   for(const over of rects.slice(i+1))pieces=pieces.flatMap(rect=>{
    const left=Math.max(rect.left,over.left),right=Math.min(rect.right,over.right),top=Math.max(rect.top,over.top),bottom=Math.min(rect.bottom,over.bottom);
    if(left>=right||top>=bottom)return [rect];const rest=[];
    if(rect.left<left)rest.push({...rect,right:left});
    if(right<rect.right)rest.push({...rect,left:right});
    if(rect.top<top)rest.push({left,right,top:rect.top,bottom:top});
    if(bottom<rect.bottom)rest.push({left,right,top:bottom,bottom:rect.bottom});
    return rest;
   });
   return pieces.map(rect=>({...rect,card:Number(el.dataset.card)}));
  });
 });
}
function crosses(a,b,rect){
 let low=0,high=1;
 for(const [start,delta,min,max] of [[a.x,b.x-a.x,rect.left,rect.right],[a.y,b.y-a.y,rect.top,rect.bottom]]){
  if(Math.abs(delta)<.0001){if(start<min||start>max)return false;continue}
  const p=(min-start)/delta,q=(max-start)/delta;low=Math.max(low,Math.min(p,q));high=Math.min(high,Math.max(p,q));
  if(low>high)return false;
 }
 return true;
}
$('hand').addEventListener('pointerdown',e=>{
 const button=e.target.closest('[data-card]');if(!button||!e.isPrimary||e.button!==0||drag)return;
 const card=Number(button.dataset.card);if(!state?.room?.players[state.room.me].hand.includes(card))return;
 drag={id:e.pointerId,card,start:{x:e.clientX,y:e.clientY},last:{x:e.clientX,y:e.clientY},before:new Set(selected),removing:selected.has(card),regions:handRegions(),moved:false};
 $('hand').setPointerCapture(e.pointerId);e.preventDefault();
});
function moveDrag(e){
 if(!drag||drag.id!==e.pointerId)return;
 const point={x:e.clientX,y:e.clientY};
 if(!drag.moved&&Math.hypot(point.x-drag.start.x,point.y-drag.start.y)<6)return;
 drag.moved=true;
 const apply=card=>drag.removing?selected.delete(card):selected.add(card);
 apply(drag.card);
 for(const rect of drag.regions)if(crosses(drag.last,point,rect))apply(rect.card);
 drag.last=point;syncSelected();e.preventDefault();
}
$('hand').addEventListener('pointermove',moveDrag);
$('hand').addEventListener('pointerup',e=>{
 if(!drag||drag.id!==e.pointerId)return;
 moveDrag(e);const old=drag;drag=null;
 if($('hand').hasPointerCapture(e.pointerId))$('hand').releasePointerCapture(e.pointerId);
 if(!old.moved)tapCard(old.card);e.preventDefault();
});
$('hand').addEventListener('pointercancel',cancelDrag);
$('hand').addEventListener('lostpointercapture',cancelDrag);
window.addEventListener('blur',cancelDrag);window.addEventListener('resize',cancelDrag);
window.addEventListener('scroll',cancelDrag,{passive:true});
// Pointer taps are handled above; retain keyboard and assistive-tech activation.
$('hand').addEventListener('click',e=>{if(e.detail===0&&!e.pointerType){const b=e.target.closest('[data-card]');if(b)tapCard(Number(b.dataset.card))}});
for(const tab of ['recharge','items','redeem'])$('tab-'+tab).onclick=()=>{for(const t of ['recharge','items','redeem']){$('tab-'+t).classList.toggle('active',t===tab);$('tab-'+t).setAttribute('aria-selected',String(t===tab));$(t+'-pane').hidden=t!==tab}};
$('recharge').onclick=async()=>{const d=await mutation('recharge',{bean_version:2,shop_version:4,yidt:price,request_key:operationKey('recharge','v4:'+price)});if(d){sessionStorage.removeItem('ddz.operation.v2.recharge');toast('充值成功，豆子及赠送道具已到账');$('wallet-dialog').close()}};$('redeem-beans').oninput=previewRedeem;$('redeem-all').onclick=()=>{$('redeem-beans').value=state?.beans||0;previewRedeem()};$('redeem').onclick=async()=>{const d=await mutation('redeem',{bean_version:2,beans:Number($('redeem-beans').value),request_key:operationKey('redeem',Number($('redeem-beans').value))});if(d){sessionStorage.removeItem('ddz.operation.v2.redeem');toast('赎回成功，YIDT 已到账');$('redeem-beans').value='';previewRedeem()}};
document.querySelectorAll('[data-buy-item]').forEach(button=>button.onclick=async()=>{
 const item=button.dataset.buyItem,input=$('buy-'+item+'-quantity'),quantity=Number(input.value);
 if(!Number.isInteger(quantity)||quantity<1||quantity>1000)return toast('购买数量须为 1～1000 的整数');
 const kind='item_'+item,d=await mutation('items',{shop_version:4,item,quantity,request_key:operationKey(kind,'v4:'+quantity)});
 if(d){sessionStorage.removeItem('ddz.operation.v2.'+kind);toast('道具已到账')}
});
$('result-share').onclick=shareFinished;
$('again').onclick=async()=>{const r=state?.room;if(!r)return;const currentBase=r.base;variant=r.variant==='double'?'happy':r.variant||'normal';$('variant').value=variant;$('result-dialog').close();const d=await mutation('leave',{});if(d){base=currentBase;await mutation('rooms',{mode:'match',base,variant,rules_version:3,use_counter:counterOption()})}};$('result-back').onclick=()=>{$('result-dialog').close();mutation('leave',{})};
$('variant').value=variant;$('variant').onchange=e=>{variant=e.target.value;DDZTheme.set(variant);tiers()};
$('room-use-counter').onchange=()=>act({action:'counter',use_counter:$('room-use-counter').checked});
$('share-room').onclick=async()=>{const url=state?.room?.share_url;if(!url)return;try{await navigator.clipboard.writeText(url);toast('邀请链接已复制，可发送到 YiChat')}catch{prompt('复制房间邀请链接',url)}};
async function initialLoad(){await load();const m=location.pathname.match(/\/room\/(\d{5})\/([a-f0-9]{32})\//);if(m&&user&&(!state?.room||['finished','closed'].includes(state.room.status))){const joined=await mutation('rooms',{mode:'join',rules_version:3,use_counter:counterOption(),code:m[1],room_id:m[2]});if(joined){$('result-dialog').close();history.replaceState(null,'',prefix+'/')}}}
tiers();packages();if(user){initialLoad();setInterval(()=>{if(!busy&&!document.hidden)load()},1200)}setInterval(updateTimer,250);document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)load()});
})();
