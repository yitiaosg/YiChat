import {useEffect,useRef,useState} from 'preact/hooks';
import {ExternalLink,RefreshCw,Gift,ArrowUpRight,ArrowLeftRight,CheckCircle2,ChevronRight} from 'lucide-preact';
import {requests,decimal,path} from './api';
import {chat} from './chat';
import {useVisible,rendererScript,ErrorText,Modal} from './shared';
const cache=new Map<string,{value:any;until:number;bytes:number}>();let cacheBytes=0;let concurrent=0;const waiters:Array<()=>void>=[];
async function limited<T>(fn:()=>Promise<T>){if(concurrent>=2)await new Promise<void>(resolve=>waiters.push(resolve));concurrent++;try{return await fn()}finally{concurrent--;waiters.shift()?.()}}
export async function preview(url:string,room:string,force=false){const key=[chat.session?.uid,room,url].join('|'),cached=cache.get(key);if(!force&&cached&&cached.until>Date.now())return cached.value;
  return requests.single('preview:'+key,()=>limited(async()=>{const uid=chat.session?.uid;const value=await requests.api('/api/preview',{url,room});if(!uid||uid!==chat.session?.uid)throw new DOMException('会话已锁定','AbortError');const bytes=JSON.stringify(value).length*2;cacheBytes-=cache.get(key)?.bytes||0;cache.delete(key);while(cache.size&&(cacheBytes+bytes>24*1024*1024||cache.size>=160)){const oldest=cache.keys().next().value!;cacheBytes-=cache.get(oldest)!.bytes;cache.delete(oldest)}if(bytes<=24*1024*1024){cache.set(key,{value,until:Date.now()+60000,bytes});cacheBytes+=bytes}return value}));
}
window.addEventListener('yc:locked',()=>{cache.clear();cacheBytes=0});
export function CardArt({data}:{data:any}){
 const ref=useRef<HTMLDivElement>(null),[error,setError]=useState('');
 useEffect(()=>{let disposed=false,renderer:any,timer:ReturnType<typeof setInterval>|undefined;
 const observer=new IntersectionObserver(([e])=>{clearInterval(timer);if(e.isIntersecting&&!matchMedia('(prefers-reduced-motion: reduce)').matches)timer=setInterval(()=>{if(!document.hidden&&renderer&&!renderer.pointer&&!renderer.pinned){renderer.rx=Math.sin(performance.now()/2600)*8;renderer.ry=Math.cos(performance.now()/3200)*14;renderer.schedule()}},100)},{threshold:0});
 rendererScript('ycards-render').then(()=>{if(!disposed&&ref.current){renderer=new (window as any).YCardRenderer(ref.current,data);observer.observe(ref.current)}}).catch(e=>setError(e.message));return()=>{disposed=true;clearInterval(timer);observer.disconnect();renderer?.destroy()}},[data.id]);
 return <><div ref={ref} class="card-art" aria-label={'YCard 实物效果 '+(data.name||data.cid)}/>{error&&<ErrorText>{error}</ErrorText>}</>
}
function GameArt({data}:{data:any}){
 const ref=useRef<HTMLCanvasElement>(null),[error,setError]=useState('');
 useEffect(()=>{let disposed=false;Promise.all([rendererScript('parachute-scene'),...['plane','jump'].map(name=>new Promise<HTMLImageElement>((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=()=>reject(Error('游戏资源缺失'));im.src='/render/parachute-'+name+'.svg'}))]).then((result)=>{
  if(disposed||!ref.current)return;const canvas=ref.current,ctx=canvas.getContext('2d')!,plane=result[1] as HTMLImageElement,jump=result[2] as HTMLImageElement;
  const w=600,h=320;canvas.width=w*2;canvas.height=h*2;ctx.scale(2,2);
  const height=Math.max(0,Number(data.exit_height)||0),layout=(window as any).YiParachuteLayout(w,h,170,height,height,data.status==='jumped');
  const bg=ctx.createLinearGradient(0,0,0,h);bg.addColorStop(0,'#07142f');bg.addColorStop(.7,'#244a71');bg.addColorStop(1,'#273c58');ctx.fillStyle=bg;ctx.fillRect(0,0,w,h);
  ctx.fillStyle='#fff1bc';ctx.beginPath();ctx.arc(515,64,24,0,Math.PI*2);ctx.fill();ctx.fillStyle='#e5f0ff';for(let i=0;i<28;i++)ctx.fillRect((i*113+37)%600,(i*73+18)%180,1.5,1.5);
  ctx.fillStyle='#0a1b35';for(let i=0;i<24;i++){const y=260+(i%4)*7;ctx.fillRect(i*28-20,y,22,320-y)}
  ctx.fillStyle='#17263d';ctx.fillRect(0,290,w,30);ctx.fillStyle='#9dadbb';ctx.fillRect(0,290,w,2);ctx.strokeStyle='#dce9f2';ctx.setLineDash([25,20]);ctx.beginPath();ctx.moveTo(0,310);ctx.lineTo(w,310);ctx.stroke();ctx.setLineDash([]);
  const px=Math.min(475,Math.max(100,layout.x)),py=h-layout.bottom-34;
  if(data.status==='jumped'){ctx.save();ctx.translate(px,py);ctx.rotate(layout.angle*Math.PI/180);ctx.drawImage(plane,-85,-35,170,70);ctx.restore();ctx.drawImage(jump,Math.max(12,px-100),Math.min(212,py+20),70,90)}
  else{const g=ctx.createRadialGradient(px,py,2,px,py,55);g.addColorStop(0,'#fff4b8');g.addColorStop(.35,'#ffca55');g.addColorStop(.7,'#ed7859');g.addColorStop(1,'#ed785900');ctx.fillStyle=g;ctx.beginPath();ctx.arc(px,py,55,0,Math.PI*2);ctx.fill();ctx.strokeStyle='#ffcd75';ctx.lineWidth=3;for(let i=0;i<12;i++){const a=i/12*Math.PI*2;ctx.beginPath();ctx.moveTo(px+Math.cos(a)*32,py+Math.sin(a)*32);ctx.lineTo(px+Math.cos(a)*65,py+Math.sin(a)*65);ctx.stroke()}}
  ctx.fillStyle='#071a32cc';ctx.fillRect(16,16,170,58);ctx.fillStyle='#edf5ff';ctx.font='600 13px system-ui';ctx.fillText('飞行高度 · 固定快照',27,36);ctx.font='700 23px system-ui';ctx.fillText(height.toFixed(2)+' m',27,62);
 }).catch(e=>setError(e.message));return()=>{disposed=true}},[data]);
 return <><canvas ref={ref} class="game-art" aria-label={`跳伞场景：${data.status==='jumped'?'安全跳伞':'飞机爆炸'}，高度 ${data.exit_height} 米`}/>{error&&<ErrorText>{error}</ErrorText>}</>
}
const status:Record<string,string>={cancelled:'已取消',pending:'到账处理中',awaiting_deposit:'待授权入金',awaiting_receipt:'待确认收款',active:'可领取',paying:'到账处理中',paid:'已到账',expired:'已截止',refunding:'退款处理中',refunded:'已退款'};
const visibleOrders=new Map<string,{count:number;room:string;set:Set<(value:any)=>void>}>();
let polling=false;
setInterval(async()=>{if(polling||document.hidden||!chat.session)return;polling=true;try{for(const [id,row] of [...visibleOrders].slice(0,30)){const o=await requests.single('order:'+id,()=>requests.api('/api/orders/'+id));for(const set of row.set)set(o)}}catch{}finally{polling=false}},20000);
window.addEventListener('yc:locked',()=>visibleOrders.clear());
function publishOrder(order:any){for(const set of visibleOrders.get(order.id)?.set||[])set(order)}
function useOrder(initial:any,room:string){
 const [order,setOrder]=useState(initial),[error,setError]=useState('');const {ref,visible}=useVisible();
 async function refresh(afterAction=false){try{if(afterAction)await requests.single('order:'+order.id,()=>Promise.resolve(null));const value=await requests.single('order:'+order.id,()=>requests.api('/api/orders/'+path(order.id)));setOrder(value);publishOrder(value);setError('');return value}catch(e:any){setError(e.message)}}
 useEffect(()=>{if(!visible)return;void refresh();if(['paid','refunded','cancelled'].includes(order.state))return;let row=visibleOrders.get(order.id);if(!row){row={room,count:0,set:new Set()};visibleOrders.set(order.id,row)}row.count++;row.set.add(setOrder);return()=>{const r=visibleOrders.get(order.id);r?.set.delete(setOrder);if(r&&!--r.count)visibleOrders.delete(order.id)}},[visible,order.id,['paid','refunded','cancelled'].includes(order.state)]);
 return {order,setOrder,error,setError,ref,refresh};
}
export function OrderBubble({initial,room,receiptSender}:{initial:any;room:string;receiptSender?:string}){
 const {order,error,ref}=useOrder(initial,room),[open,setOpen]=useState(false);
 const receipt=!!receiptSender,confirmed=order.kind==='direct'&&order.claims?.some((p:any)=>p.user_id===order.recipient_uid),verified=confirmed&&receiptSender===order.recipient_mxid;
 const group=order.kind==='group',received=order.state==='paid',heading=receipt?(verified?'已收款':'收款回执未核实'):group?order.title:order.amount+' YIDT';
 const label=!receipt&&chat.readOnly(room)?'会话只读 · '+(status[order.state]||order.state):receipt?(verified?(received?'已到账':'到账处理中'):'请查看原订单状态'):order.state==='awaiting_receipt'?(order.actions?.receive?'待收款 · 点击确认':'待对方收款'):status[order.state]||order.state;
 return <div ref={ref} class={'money-message '+(group?'packet-message':'transfer-message')+(receipt?' receipt-message':'')} data-order-id={order.id} data-receipt={receipt?'true':undefined}><button type="button" class="money-bubble" onClick={()=>setOpen(true)} aria-label={receipt?'查看收款回执':group?'打开群红包':'打开转账'}><span class="money-main"><span class="money-icon">{receipt?<CheckCircle2 size={29}/>:group?<Gift size={29}/>:<ArrowLeftRight size={29}/>}</span><span><strong>{heading}</strong><small>{receipt?order.amount+' YIDT · '+label:group?order.count+' 份 · '+label:label}</small></span></span><span class="money-footer">{group?'YIDT 红包':'YIDT 转账'}<span>{receipt?'收款回执':'查看详情'}</span></span></button>{error&&<ErrorText>{error}</ErrorText>}{open&&<Modal title={group?'群红包':receipt?'收款回执':'转账'} close={()=>setOpen(false)}><OrderCard initial={order} room={room} onFunded={()=>setOpen(false)}/></Modal>}</div>
}
export function OrderCard({initial,room,onFunded,onCancelled,confirmFund=false}:{initial:any;room:string;onFunded?:()=>void;onCancelled?:()=>void;confirmFund?:boolean}){
 const {order,setOrder,error,setError,ref,refresh}=useOrder(initial,room),[busy,setBusy]=useState(false),[confirm,setConfirm]=useState(confirmFund&&initial.actions?.fund?'fund':'');
 const readOnly=chat.readOnly(room);
 useEffect(()=>{if(confirm&&!order.actions?.[confirm])setConfirm('')},[order.state]);
 async function action(which:string){
  if(readOnly){setError('此会话已只读，不能继续交易操作');return}
  setBusy(true);setError('');
  try{if(which==='claim')await new Promise(resolve=>setTimeout(resolve,500));const result=await requests.api('/api/orders/'+path(order.id)+'/'+which,['fund','cancel'].includes(which)?{}:{room});setOrder(result);publishOrder(result);setConfirm('');if(which==='cancel'){await chat.recordCancelledOrder(result);onCancelled?.()}if(which==='receive')await chat.announceReceipt(result);if(which==='fund'){await chat.announceOrder(result);onFunded?.()}}
  catch(e:any){try{const current=await refresh(true);if(which==='cancel'&&current?.state==='cancelled'){await chat.recordCancelledOrder(current);setConfirm('');setError('');onCancelled?.()}else if(which==='receive'&&current?.claims?.some((p:any)=>p.user_id===chat.session.uid)){await chat.announceReceipt(current);setConfirm('');setError('')}else if(which==='claim'&&current?.my_amount){setConfirm('');setError('')}else if(which==='fund'&&current?.deposit){await chat.announceOrder(current);setConfirm('');setError('');onFunded?.()}else setError(e.message)}catch(recovery:any){setError(recovery.message)}}
  finally{setBusy(false)}
 }
 async function receipt(){setBusy(true);try{const current=await refresh(true);if(current)await chat.announceReceipt(current)}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 return <div ref={ref} class="order-card"><div class="share-eyebrow"><Gift size={16}/>{order.kind==='direct'?'YIDT 转账':'YIDT 群红包'}<span>{status[order.state]||order.state}</span></div><h3>{order.title}</h3><div class="order-amount">{order.amount} <small>YIDT</small></div><p class="muted">{order.sender} {order.recipient?'→ '+order.recipient:'· '+order.count+' 份'}</p><p class="small">付款钱包 {order.source}{order.destination&&<> → {order.destination}</>}</p><p class="small">入金手续费 {order.incoming_fee} · 出金费用预留 {order.outgoing_fee_reserve} YIDT</p><p class="small">{order.state==='cancelled'?'未入金，已取消':order.expires?'截止 '+new Date(order.expires*1000).toLocaleString('zh-CN',{})+'（入金后 24 小时）':'授权并入金后开始 24 小时倒计时'}</p>
 {order.my_amount&&<p>本人已分配：{order.my_amount} YIDT</p>}{order.best&&<p>手气最佳：{order.best}</p>}
 {order.claims?.length>0&&<details><summary>领取记录（{order.claims.length}）</summary>{order.claims.map((c:any)=><p key={c.user_id}>{c.user} · {c.amount} YIDT · {status[c.state]||c.state}</p>)}</details>}
 {order.refund&&<p>退款本金 {decimal(order.refund.principal)} YIDT · {status[order.refund.state]||order.refund.state}</p>}
 {order.state==='cancelled'&&<p class="notice">本次订单已取消，不再入金或发送。需要转账或发红包时请重新创建订单。</p>}
 <div class="actions">{!readOnly&&order.authorize_url&&<a class="button" href={order.authorize_url}>前往 YIDT 授权 <ExternalLink size={14}/></a>}
 {!readOnly&&order.actions?.cancel&&order.room===room&&<button type="button" disabled={busy} onClick={()=>setConfirm('cancel')}>取消订单</button>}
 {['fund','receive','claim'].filter(a=>!readOnly&&order.actions?.[a]&&order.room===room).map(a=><button type="button" disabled={busy} onClick={()=>setConfirm(a)} key={a}>{a==='fund'?'已批准，执行入金':a==='receive'?'确认收款':'领取红包'}</button>)}<button type="button" class="text-button" disabled={busy} onClick={()=>refresh()}><RefreshCw size={14}/>刷新状态</button></div>
 {confirm&&!readOnly&&<div class="confirm"><p>{confirm==='cancel'?'确认取消本次订单？取消后不再入金、不发送转账或红包气泡。已入金订单不能取消。':confirm==='fund'?`确认已在 YIDT 批准，现执行 ${order.amount} YIDT 入金及上述费用。打开授权页本身不代表批准。`:confirm==='receive'?`确认收取 ${order.amount} YIDT 到订单指定钱包。`:'确认领取此红包，金额由服务器随机分配。'}</p><button type="button" class="primary" disabled={busy} onClick={()=>action(confirm)}>确认{confirm==='cancel'?'取消订单':confirm==='fund'?'入金':confirm==='receive'?'收款':'领取'}</button><button type="button" disabled={busy} onClick={()=>setConfirm('')}>{confirm==='cancel'?'保留订单':'返回'}</button></div>}
 {!readOnly&&order.kind==='direct'&&order.recipient_uid===chat.session.uid&&order.claims?.some((p:any)=>p.user_id===chat.session.uid)&&!chat.business.some(b=>b.type==='receipt'&&b.result?.id===order.id&&b.state==='announced')&&<button type="button" disabled={busy} class="text-button" onClick={receipt}>补发收款回执</button>}
 {!readOnly&&order.deposit&&order.sender_uid===chat.session.uid&&chat.business.some(b=>b.type==='order'&&b.result?.id===order.id&&b.state!=='announced')&&<button type="button" disabled={busy} onClick={async()=>{try{await chat.announceOrder(order);onFunded?.()}catch(e:any){setError(e.message)}}}>发送转账 / 红包气泡</button>}
 {busy&&<p role="status">正在处理，请勿重复点击…</p>}{error&&<ErrorText>{error}</ErrorText>}<a class="share-original" href={order.url||'https://chat.yitiao.org/orders/'+order.id}>查看原订单 <ArrowUpRight size={14}/></a></div>
}
export function ShareCard({url,room,receiptSender}:{url:string;room:string;receiptSender?:string}){
 const {ref,visible}=useVisible(),[data,setData]=useState<any>(),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 async function load(force=false){setLoading(true);try{const value=await preview(url,room,force);setData(value);setError(value?'':'资源未公开、已失效或无权查看，可打开原链接')}catch(e:any){setError(e.message)}finally{setLoading(false)}}
 useEffect(()=>{if(visible&&data===undefined&&!loading&&!error)void load();if(!visible&&data?.type==='card'){const timer=setTimeout(()=>setData(undefined),60000);return()=>clearTimeout(timer)}},[visible,data]);
 return <div ref={ref} class="share-card" data-share-type={data?.type||'pending'}>{data?.type==='game'?<>{visible?<GameArt data={data.data}/>:<div class="game-art" aria-hidden="true"/>}<div class="share-content"><div class="share-eyebrow">PARACHUTE · 固定结果快照</div><h3>{data.data.status==='jumped'?'安全跳伞':'飞机爆炸'}</h3><p>跳伞 / 结束高度 {data.data.exit_height} m · 爆炸高度 {data.data.crash_height} m</p></div></>:data?.type==='card'?<>{visible?<CardArt data={data.data}/>:<div class="card-art" aria-hidden="true"/>}<div class="share-content"><div class="share-eyebrow">YCARD · 真实实例</div><h3>{data.data.name}</h3><p class="small">{data.data.cid}</p></div></>:data?.type==='transaction'?<div class="share-content"><div class="share-eyebrow">YIDT · 交易</div><h3>{decimal(String(data.data.credit))} YIDT</h3><p class="small">{data.data.source} → {data.data.destination}</p><p class="small">{new Date(data.data.created*1000).toLocaleString('zh-CN',{})} · {data.data.hash.slice(0,10)}…{data.data.hash.slice(-8)}</p></div>:data?.type==='order'?<OrderBubble initial={data.data} room={room} receiptSender={receiptSender}/>:<div class="share-content"><p class="muted">{loading?'正在获取分享…':error||'分享将在可见时加载'}</p>{error&&<button type="button" class="text-button" onClick={()=>load(true)}>重试预览</button>}</div>}
 {data?.type!=='order'&&<a class="share-original" href={url} target="_blank" rel="noopener noreferrer">打开原链接 <ArrowUpRight size={14}/></a>}</div>
}
export function GiftCard({id,room}:{id:string;room:string}){
 const {ref,visible}=useVisible(),[gift,setGift]=useState<any>(),[error,setError]=useState(''),[open,setOpen]=useState(false),[destroyed,setDestroyed]=useState(false);
 async function load(){setError('');try{const value=await requests.single('gift:'+room+':'+id,()=>requests.api('/api/cards/gifts/'+path(id)+'?room='+path(room)));if(value.id!==id||value.room!==room||value.state!=='transferred')throw Error('赠卡记录与当前会话不匹配');setDestroyed(false);setGift(value)}catch(e:any){setGift(undefined);setOpen(false);setDestroyed(e.status===410);setError(e.message)}}
 useEffect(()=>{if(!visible)return;const refresh=()=>{if(!document.hidden)void load()};refresh();const timer=setInterval(refresh,20000);window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);return()=>{clearInterval(timer);window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh)}},[visible,id,room]);
 return <div ref={ref} class="gift-message" data-gift-id={id}>
  <div class="gift-bubble">
   <div class="gift-heading"><span class="gift-icon"><Gift size={21}/></span><strong>赠送卡片</strong><span class="gift-state">{gift?'已赠送':destroyed?'已销毁':error?'暂不可用':'核实中'}</span></div>
   <div class="gift-art">{gift&&visible?<CardArt data={gift.card}/>:<div class="card-art gift-placeholder"><Gift size={32}/><span>{destroyed?'卡片已销毁':error?'卡片预览暂不可用':gift?'YCard 卡片预览':'正在获取卡片…'}</span></div>}</div>
   <div class="gift-summary">{gift?<><h3>{gift.card.name}</h3><p>{gift.sender} → {gift.recipient}</p></>:<p class="muted">{error||'正在核实赠送记录…'}</p>}{error&&!destroyed&&<button type="button" class="text-button" onClick={load}>重试赠卡预览</button>}</div>
   <button type="button" class="gift-footer" disabled={!gift} aria-label="查看赠卡详情" onClick={()=>setOpen(true)}><span>YCard · {gift?'赠送已完成':'赠卡消息'}</span><span>查看详情 <ChevronRight size={14}/></span></button>
  </div>
  {open&&gift&&<Modal title="赠卡详情" close={()=>setOpen(false)}><CardArt data={gift.card}/><h3 class="gift-detail-title">{gift.card.name}</h3><p>{gift.sender} → {gift.recipient}</p><p class="small muted">{new Date(gift.created*1000).toLocaleString()} · 当前卡片</p><p class="small gift-cid">{gift.card.cid}</p></Modal>}
 </div>
}
