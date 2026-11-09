import {useEffect,useRef,useState} from 'preact/hooks';
import QRCode from 'qrcode';
import {ArrowLeft,ExternalLink} from 'lucide-preact';
import {requests,path} from './api';
import {chat} from './chat';
import {isApp} from './app';
import {Avatar} from './profiles';
import {Media} from './conversation';
import {ShareCard} from './previews';
import {ErrorText,TextLinks} from './shared';
import {messageBody} from './message-display';
import {recordUrl,verifyRecord,type IdentityDisplay} from './record-model';
function PublicAvatar({hash,index,name}:{hash:string;index:number;name:string}){
 const [src,setSrc]=useState(''),[pending,setPending]=useState(true);
 useEffect(()=>{let active=true,url='';setPending(true);void requests.fetch('/api/public-records/'+path(hash)+'/avatars/'+index).then(async r=>{if(!r.ok)return;const blob=await r.blob();if(active){url=URL.createObjectURL(blob);setSrc(url)}}).catch(()=>{}).finally(()=>{if(active)setPending(false)});return()=>{active=false;if(url)URL.revokeObjectURL(url)}},[hash,index]);
 return <span class="avatar snapshot-avatar" data-avatar-loading={pending?'true':undefined} aria-label={name}>{src?<img src={src} alt=""/>:name.slice(0,1)}</span>;
}
export function SnapshotMessage({item,identity='both',showIdentity=true,mine=false,publicHash,index=0}:{item:any;identity?:IdentityDisplay|boolean;showIdentity?:boolean;mine?:boolean;publicHash?:string;index?:number}){
 const mode=identity===true?'both':identity===false?'none':identity,profile=chat.profiles.get(item.sender),name=item.username?item.name:(profile?.nickname||item.name||chat.name(item.sender));
 const avatar=mode==='both'||mode==='avatar',nickname=mode==='both'||mode==='name';
 const content=item.content,urls=(item.previews||[]).map((p:any)=>p.url),body=messageBody(content,urls),media=['m.image','m.file','m.audio','m.video'].includes(content.msgtype);
 return <article class={'message snapshot-message'+(mine?' mine':'')+(avatar&&!showIdentity?' snapshot-continued':'')}>
 {avatar&&showIdentity&&(publicHash?<PublicAvatar hash={publicHash} index={index} name={name}/>:<Avatar mxid={item.sender} label={name} eager className="snapshot-avatar" srcUrl={item.avatar_url||profile?.avatar_url}/>)}
 <div class="message-main">{nickname&&showIdentity&&<strong class="snapshot-name">{name}</strong>}<div class={'bubble'+(urls.length||media?' content-bubble':'')}>{media?<Media content={content} eager publicRecord={publicHash}/>:body&&<p class="message-body"><TextLinks body={body}/></p>}{(item.previews||[]).map((p:any)=><ShareCard url={p.url} room="" snapshot={p}/>)}<small class="snapshot-time muted">{new Date(item.sent_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}</small></div></div>
 </article>;
}
export function RecordTranscript({payload,publicHash}:{payload:any;publicHash?:string}){
 const identity:IdentityDisplay=payload.identity||'both';const avatar=identity==='both'||identity==='avatar';
 return <div data-record-identity={identity} class={'record-transcript'+(avatar?' has-record-avatars':'')}>{payload.messages.map((m:any,i:number)=><SnapshotMessage key={m.id||i} item={m} index={i} publicHash={publicHash} identity={identity} mine={m.sender===(payload.owner||payload.messages[0]?.sender)} showIdentity={!i||payload.messages[i-1].sender!==m.sender}/>)}</div>;
}
export function RecordQRCode({url}:{url:string}){
 const [src,setSrc]=useState(''),[error,setError]=useState('');
 useEffect(()=>{let active=true;setSrc('');void QRCode.toDataURL(url,{width:240,margin:4,errorCorrectionLevel:'M'}).then(src=>{if(active)setSrc(src)}).catch(e=>{if(active)setError(e.message)});return()=>{active=false}},[url]);
 return <footer class="record-qr" data-qr-loading={!src&&!error?'true':undefined}>{src&&<img src={src} alt="扫描二维码查看聊天记录" data-qr-url={url}/>}<div><strong>扫码查看聊天记录</strong><p>引用消息七天内有效</p><small>{url}</small></div>{error&&<ErrorText>{error}</ErrorText>}</footer>;
}
export function RecordOpenLink({hash}:{hash:string}){return <a class="button record-open" href={recordUrl(hash)} target={isApp?'_self':'_blank'} rel="noopener noreferrer"><ExternalLink size={16}/>{isApp?'全屏显示':'在新标签页打开'}</a>}
export function RecordPage({hash}:{hash:string}){
 const [record,setRecord]=useState<any>(),[error,setError]=useState('');
 useEffect(()=>{const theme=localStorage.getItem('yc:theme')||'system';document.documentElement.dataset.theme=theme==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):theme;let active=true;void requests.api('/api/public-records/'+path(hash)).then(async r=>{const payload=await verifyRecord(hash,r.canonical);if(active)setRecord({...r,payload})}).catch(e=>{if(active)setError(e.message)});return()=>{active=false}},[hash]);
 return <main class="record-page"><header><button class="icon" aria-label="返回" onClick={()=>history.length>1?history.back():location.assign('/')}><ArrowLeft size={20}/></button><h1>聊天记录</h1><a href="/">YiChat</a></header>{record?<><p class="record-expiry muted">{record.payload.messages.length} 条消息 · 有效至 {new Date(record.expires*1000).toLocaleString('zh-CN')}</p><RecordTranscript payload={record.payload} publicHash={hash}/><small class="hash">SHA-256 {hash}</small></>:error?<ErrorText>{error}</ErrorText>:<p role="status">正在读取聊天记录…</p>}</main>;
}
