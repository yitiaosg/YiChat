import {useEffect,useState} from 'preact/hooks';
import {chat} from './chat';
import {requests} from './api';
import {useVisible} from './shared';
const avatars=new Map<string,Promise<string>>(),urls=new Set<string>();
window.addEventListener('yc:locked',()=>{for(const url of urls)URL.revokeObjectURL(url);urls.clear();avatars.clear()});
async function avatar(url:string){let task=avatars.get(url);if(!task){task=(async()=>{const uid=chat.session?.uid,r=await requests.fetch(url);if(!r.ok)throw Error('头像暂不可用');const blob=await r.blob();if(!chat.session||chat.session.uid!==uid)throw Error('会话已锁定');const local=URL.createObjectURL(blob);urls.add(local);return local})().catch(e=>{avatars.delete(url);throw e});avatars.set(url,task)}return task}
export function Avatar({mxid,label='',className=''}:{mxid?:string;label?:string;className?:string}){const {ref,visible}=useVisible(),[src,setSrc]=useState('');const profile=mxid&&chat.profiles.get(mxid),url=profile&&profile.avatar_url;useEffect(()=>{let disposed=false;setSrc('');if(visible&&url)void avatar(url).then(value=>{if(!disposed)setSrc(value)}).catch(()=>{});return()=>{disposed=true}},[visible,url]);return <span ref={ref} class={'avatar '+className} aria-label={label||profile&&profile.nickname||'头像'}>{src?<img src={src} alt=""/>:(label||profile&&profile.nickname||chat.name(mxid)).slice(0,1)}</span>}
