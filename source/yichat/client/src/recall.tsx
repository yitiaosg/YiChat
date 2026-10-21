import {isApp} from './app';
import {useEffect,useState} from 'preact/hooks';import {Shield,RefreshCw} from 'lucide-preact';import {sdk,chat} from './chat';import {requests,sleep} from './api';import {verifyPacket} from './history';
const entries=new Map<string,{raw:any;ok:boolean}>(),pending=new Map<string,Promise<boolean>>();
window.addEventListener('yc:locked',()=>{entries.clear();pending.clear()});
async function check(id:string):Promise<boolean>{
 let cached=entries.get(id);if(cached?.ok)return true;if(!cached&&isApp){const raw=await chat.businessVault.state('recall:'+id);if(raw)cached={raw,ok:false}}
 if(!cached){const [p]=await requests.single('recall:'+id,()=>requests.api('/api/history/recall-ciphertexts',{ids:[id]}));if(!p?.available)return false;const raw=await verifyPacket(p.raw,{event_id:id,room:p.room,sha256:p.sha256});cached={raw,ok:false};if(isApp)await chat.businessVault.saveState('recall:'+id,raw);entries.set(id,cached);if(entries.size>300)entries.delete(entries.keys().next().value!)}
 for(let retry=0;retry<3;retry++){const event=new sdk.MatrixEvent(cached.raw);await chat.client.decryptEventIfNeeded(event);cached.ok=event.getType()==='m.room.message'&&!event.isDecryptionFailure();if(cached.ok)return true;if(retry<2)await sleep(1500)}return false;
}
export function Recall({id,security}:{id:string;security:()=>void}){
 const [ok,setOk]=useState(entries.get(id)?.ok||false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function run(){setBusy(true);setError('');try{let task=pending.get(id);if(!task){task=check(id).finally(()=>pending.delete(id));pending.set(id,task)}setOk(await task)}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 useEffect(()=>{const observer=new IntersectionObserver(([entry])=>{if(entry.isIntersecting){observer.disconnect();void run()}});const node=document.getElementById('recall-'+id);if(node)observer.observe(node);return()=>observer.disconnect()},[id]);
 return <div id={'recall-'+id} class="recalled-encrypted" data-recall-unlocked={ok?'true':'false'}>{ok?<p class="muted">消息已撤回</p>:<div class="decryption-error"><Shield size={17}/><p>{busy?'正在核实本机历史密钥…':'无法解密此加密消息，需要恢复历史密钥。'}</p><button type="button" onClick={security}>恢复历史密钥</button><button type="button" disabled={busy} onClick={run}><RefreshCw size={14}/>重新核实密钥</button>{error&&<p class="small error">{error}</p>}</div>}</div>
}
