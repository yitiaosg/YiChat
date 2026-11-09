import {useEffect,useState} from 'preact/hooks';
import {chat} from './chat';
import {requests} from './api';
import {useVisible} from './shared';
const avatars=new Map<string,Promise<string>>(),urls=new Set<string>();
const IMMUTABLE=/^\/api\/(avatars\/[^/?]+\?v=\d+|groups\/[^/]+\/avatar\/[a-f0-9]{64})$/;
window.addEventListener('yc:locked',()=>{for(const url of urls)URL.revokeObjectURL(url);urls.clear();avatars.clear()});
async function avatar(url:string){let task=avatars.get(url);if(!task){task=(async()=>{const uid=chat.session?.uid,vault=chat.businessVault;
  // Avatar URLs are immutable (?v=<mtime> or a content hash), so a stored copy is current until the URL changes.
  const stable=IMMUTABLE.test(url);let blob=stable?await vault.image(url).catch(()=>undefined):undefined;
  if(!blob){const r=await requests.fetch(url);if(!r.ok)throw Error('头像暂不可用');blob=await r.blob();if(stable&&chat.session?.uid===uid)void vault.saveImage(url,blob).catch(()=>{})}
  if(!chat.session||chat.session.uid!==uid)throw Error('会话已锁定');const local=URL.createObjectURL(blob);urls.add(local);return local})().catch(e=>{avatars.delete(url);throw e});avatars.set(url,task)}return task}
export function Avatar({mxid,label='',className='',srcUrl,eager=false}:{mxid?:string;label?:string;className?:string;srcUrl?:string;eager?:boolean}){const {ref,visible}=useVisible(),[src,setSrc]=useState(''),[settled,setSettled]=useState('');const profile=mxid&&chat.profiles.get(mxid),url=srcUrl||(profile&&profile.avatar_url);useEffect(()=>{let disposed=false;setSrc('');if((eager||visible)&&url){void avatar(url).then(value=>{if(!disposed)setSrc(value)}).catch(()=>{}).finally(()=>{if(!disposed)setSettled(url||'')})}else setSettled(url||'');return()=>{disposed=true}},[eager,visible,url]);return <span ref={ref} data-avatar-loading={eager&&url&&settled!==url?'true':undefined} class={'avatar '+className} aria-label={label||profile&&profile.nickname||'头像'}>{src?<img src={src} alt=""/>:(label||profile&&profile.nickname||chat.name(mxid)).slice(0,1)}</span>}

export function RoomAvatar({room,className=''}:{room:any;className?:string}){const info=chat.info.get(room.roomId);if(info?.kind==='direct')return <Avatar mxid={info.owner===chat.session.mxid?info.peer!:info.owner} label={chat.roomName(room)} className={className}/>;const match=room.getMyMembership()==='join'&&/^mxc:\/\/chat\.yitiao\.org\/ycga_([a-f0-9]{64})$/.exec(room.currentState.getStateEvents('m.room.avatar','')?.getContent().url||'');return <Avatar label={chat.roomName(room)} className={'group-avatar '+className} srcUrl={match?'/api/groups/'+encodeURIComponent(room.roomId)+'/avatar/'+match[1]:undefined}/>;}
