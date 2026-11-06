// Web-version-only features: download links, browser notifications (Web Push)
// and developer API key management. Every entry point returns early inside
// the Android/desktop Apps (isApp), so App behaviour is unchanged. The
// developer API itself is never used by this client.
import type {ComponentChildren} from 'preact';
import {useEffect,useState} from 'preact/hooks';
import {isApp} from './app';
import {requests,path} from './api';
import {Modal,ErrorText} from './shared';

const RELEASES='https://github.com/yitiaosg/YiChat/releases';
const ROOM=/^![A-Za-z0-9._=:-]{1,255}$/;
const FLAG='yc:webpush';

function flag(value?:boolean){
 try{if(value===undefined)return localStorage.getItem(FLAG)==='1';if(value)localStorage.setItem(FLAG,'1');else localStorage.removeItem(FLAG)}catch{}
 return false;
}

export function DownloadLink({icon,className}:{icon?:ComponentChildren;className?:string}){
 const [info,setInfo]=useState<any>();
 useEffect(()=>{if(isApp)return;void fetch('/api/downloads',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():undefined).then(setInfo).catch(()=>{})},[]);
 if(isApp)return null;
 const rec=info?.recommended;
 const href=typeof rec?.url==='string'&&rec.url.startsWith('https://github.com/')?rec.url:RELEASES;
 return <a class={className} href={href} target="_blank" rel="noopener noreferrer" title={rec?.version||'GitHub Releases'}>{icon}{rec?.label||'在 GitHub 下载 YiChat'}</a>;
}

// ---------- Web Push ----------

export function webPushSupported(){
 return !isApp&&window.isSecureContext&&'serviceWorker' in navigator&&'PushManager' in window&&'Notification' in window;
}

function bytes(b64:string){
 const s=atob(b64.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-b64.length%4)%4));
 return Uint8Array.from(s,c=>c.charCodeAt(0));
}

async function workerRegistration(){
 // Own scope /push/: never controls or intercepts the chat page.
 const reg=await navigator.serviceWorker.register('/push/sw.js',{scope:'/push/'});
 if(reg.active)return reg;
 const worker=reg.installing||reg.waiting;
 if(worker)await new Promise<void>(resolve=>{const done=()=>{if(worker.state==='activated'){worker.removeEventListener('statechange',done);resolve()}};worker.addEventListener('statechange',done);done()});
 return reg;
}

async function existing(){
 const reg=await navigator.serviceWorker.getRegistration('/push/');
 return reg?await reg.pushManager.getSubscription():null;
}

export async function enableWebPush(){
 if(!webPushSupported())throw Error('此浏览器不支持网页通知');
 const permission=await Notification.requestPermission();
 if(permission!=='granted')throw Error('浏览器未允许通知。请在地址栏左侧的网站设置中允许 chat.yitiao.org 发送通知后重试');
 const {public_key}=await requests.api<{public_key:string}>('/api/webpush/config');
 const key=bytes(public_key);
 const reg=await workerRegistration();
 let sub=await reg.pushManager.getSubscription();
 const current=sub?.options?.applicationServerKey?new Uint8Array(sub.options.applicationServerKey):undefined;
 if(sub&&(!current||current.length!==key.length||current.some((v,i)=>v!==key[i]))){await sub.unsubscribe();sub=null}
 sub=sub||await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
 await requests.api('/api/webpush/subscriptions',sub.toJSON());
 flag(true);
}

export async function disableWebPush(){
 flag(false);
 if(!webPushSupported())return;
 const sub=await existing();
 if(sub){await requests.api('/api/webpush/subscriptions/remove',{endpoint:sub.endpoint}).catch(()=>{});await sub.unsubscribe()}
}

// After each Web login the subscription is re-bound to the new browser session.
export async function syncWebPush(){
 if(!webPushSupported()||!flag()||Notification.permission!=='granted')return;
 const sub=await existing();
 if(sub)await requests.api('/api/webpush/subscriptions',sub.toJSON());else await enableWebPush();
}

// Notification clicks: focus an open tab (message) or open /?room=<id>.
export function installWebPushBridge(){
 if(isApp)return()=>{};
 try{const room=new URLSearchParams(location.search).get('room');if(room&&ROOM.test(room))sessionStorage.setItem('yc:pending-room',room)}catch{}
 if(!('serviceWorker' in navigator))return()=>{};
 const relay=(e:MessageEvent)=>{const d=e.data;if(d&&d.type==='yc:open-room'&&typeof d.room==='string'&&ROOM.test(d.room))window.dispatchEvent(new CustomEvent('yc:open-room',{detail:d.room}))};
 navigator.serviceWorker.addEventListener('message',relay);
 return()=>navigator.serviceWorker.removeEventListener('message',relay);
}

export function takePendingRoom(){
 if(isApp)return '';
 try{const room=sessionStorage.getItem('yc:pending-room')||'';sessionStorage.removeItem('yc:pending-room');if(room&&new URLSearchParams(location.search).has('room'))history.replaceState(null,'','/');return ROOM.test(room)?room:''}catch{return ''}
}

export function WebNotifications({close}:{close:()=>void}){
 const [state,setState]=useState<'loading'|'unsupported'|'denied'|'off'|'on'>('loading'),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function refresh(){
  if(!webPushSupported()){setState('unsupported');return}
  if(Notification.permission==='denied'){setState('denied');return}
  setState(flag()&&Notification.permission==='granted'&&await existing()?'on':'off');
 }
 useEffect(()=>{void refresh().catch(e=>setError(e.message))},[]);
 async function toggle(on:boolean){setBusy(true);setError('');try{if(on)await enableWebPush();else await disableWebPush()}catch(e:any){setError(e.message)}finally{setBusy(false);await refresh().catch(()=>{})}}
 return <Modal title="浏览器通知" close={close}>
  {state==='unsupported'&&<p>此浏览器不支持网页通知。iPhone / iPad 需要 iOS 16.4 以上，并先用 Safari 把 YiChat 添加到主屏幕后再打开。</p>}
  {state==='denied'&&<p>浏览器已拒绝 chat.yitiao.org 的通知权限。请在地址栏左侧的网站设置中改为“允许”，然后重新打开此页面。</p>}
  {(state==='on'||state==='off')&&<label class="check"><input type="checkbox" checked={state==='on'} disabled={busy} onChange={e=>void toggle(e.currentTarget.checked)}/>在此浏览器接收新消息通知</label>}
  <p class="small muted">开启后，即使关闭 YiChat 标签页，浏览器仍会在收到新消息时提醒你，显示发送者和消息预览；点击通知可回到对应会话。归档和免打扰的会话、你自己发送的消息不会提醒。通知内容加密后经浏览器厂商的推送服务送达。退出登录后此浏览器的通知随之停止，重新登录后自动恢复。</p>
  {state==='loading'&&<p role="status">正在读取通知状态…</p>}{busy&&<p role="status">正在处理…</p>}{error&&<ErrorText>{error}</ErrorText>}
 </Modal>;
}

// ---------- Developer API keys ----------

type Key={id:string;name:string;prefix:string;created:number;last_used:number|null};
const when=(t:number|null)=>t?new Date(t*1000).toLocaleString():'从未使用';

export function ApiKeys({close}:{close:()=>void}){
 const [keys,setKeys]=useState<Key[]>([]),[limit,setLimit]=useState(3),[docs,setDocs]=useState(''),[name,setName]=useState(''),[created,setCreated]=useState<any>(),[copied,setCopied]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function load(){const r=await requests.api<any>('/api/developer/keys');setKeys(r.keys);setLimit(r.limit);setDocs(r.docs_url)}
 useEffect(()=>{void load().catch(e=>setError(e.message))},[]);
 async function run(task:()=>Promise<void>){setBusy(true);setError('');try{await task();await load()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 const create=()=>run(async()=>{const r=await requests.api('/api/developer/keys',{name:name.trim()});setCreated(r);setCopied(false);setName('')});
 const remove=(k:Key)=>{if(confirm(`删除 API Key “${k.name}”？使用它的程序将立即无法访问，已建立的 WebSocket 会在 10 秒内断开。`))void run(async()=>{await requests.api('/api/developer/keys/'+path(k.id),undefined,'DELETE')})};
 async function copy(){try{await navigator.clipboard.writeText(created.key);setCopied(true)}catch{setError('无法写入剪贴板，请手动选中复制')}}
 return <Modal title="开发者 API" close={close}>
  <p class="small muted">API Key 可用于读取你的会话列表（含归档）和服务器保存的消息、下载图片和文件、向私聊和群聊发送文字、图片和文件，并通过 WebSocket 订阅新消息。Key 可以以你的名义发消息，请像密码一样保管。不能管理会话或操作资金。限速：同一账户 5 次/秒（读取、发送、上传、下载合计）；每个账户最多 {limit} 个 Key、3 个同时运行的订阅。只能在网页版申请和管理。</p>
  {docs&&<p><a href={docs} target="_blank" rel="noopener noreferrer">查看 API 文档（GitHub）</a></p>}
  {created&&<div class="notice" role="status"><p><strong>请立即复制并妥善保存。此 Key 只显示这一次，关闭后无法再次查看。</strong></p><p><code style="word-break:break-all;user-select:all">{created.key}</code></p><div class="actions"><button type="button" onClick={()=>void copy()}>{copied?'已复制':'复制'}</button><button type="button" onClick={()=>setCreated(undefined)}>我已保存</button></div></div>}
  <h3>我的 API Key（{keys.length}/{limit}）</h3>
  {keys.length?keys.map(k=><div class="actions" key={k.id}><span><strong>{k.name}</strong> <code>{k.prefix}…</code><br/><small class="muted">创建于 {when(k.created)} · 最近使用 {when(k.last_used)}</small></span><button type="button" class="danger" disabled={busy} onClick={()=>remove(k)}>删除</button></div>):<p class="small muted">还没有 API Key。</p>}
  {keys.length<limit&&<><label>新 Key 名称<input value={name} maxLength={40} placeholder="例如：我的机器人" onInput={e=>setName(e.currentTarget.value)}/></label>
  <button type="button" class="primary" disabled={busy||!name.trim()} onClick={()=>void create()}>创建 API Key</button></>}
  {error&&<ErrorText>{error}</ErrorText>}
 </Modal>;
}
