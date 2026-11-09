import {requests} from './api';import {isApp,appCall} from './app';import {chat} from './chat';
import {sha256,materialHash,type MaterialState} from './packet-protocol';
export const MATERIAL_KEY='org.yitiao.material';
export type MaterialDocument={version:2;kind:'share'|'media'|'record';content:any;previews:any[];record?:any;files:any[]};
export type SavedMaterial={meta:MaterialState;canonical:string;document:MaterialDocument};
const memory=new Map<string,SavedMaterial>(),pending=new Map<string,Promise<SavedMaterial>>();
const online=()=>!chat.offline&&navigator.onLine;
async function checked(eid:string,value:any):Promise<SavedMaterial>{if(!value||typeof value.canonical!=='string'||value.meta?.event_id!==eid)throw Error('原始消息尚未保存到设备');const hash=await sha256(value.canonical);if(hash!==value.meta.payload_sha256)throw Error('设备原始消息数据损坏');if(value.meta.finalized&&await materialHash(value.meta,hash,value.meta.files||[])!==value.meta.sha256)throw Error('设备原始消息时间摘要损坏');return {...value,document:JSON.parse(value.canonical)}}
export async function readMaterial(eid:string){const existing=memory.get(eid);if(existing)return checked(eid,existing);let value:any;try{value=isApp&&chat.nativeProtocol?await appCall('materialRead',{event:eid}):await chat.businessVault.state('material:'+eid)}catch{}if(value){const good=await checked(eid,value);memory.set(eid,good);return good}}
async function save(eid:string,value:any){const good=await checked(eid,value);if(isApp&&chat.nativeProtocol)await appCall('materialSave',{event:eid,meta:good.meta,canonical:good.canonical});else await chat.businessVault.saveState('material:'+eid,{meta:good.meta,canonical:good.canonical});memory.set(eid,good);window.dispatchEvent(new CustomEvent('yc:material',{detail:eid}));return good}
export function ensureMaterial(eid:string):Promise<SavedMaterial>{const old=pending.get(eid);if(old)return old;const task=(async()=>{
 let local:SavedMaterial|undefined;try{local=await readMaterial(eid)}catch{/* Recompute rather than trusting a corrupt cache. */}
 if(!online()){if(local)return local;throw Error('本机尚未保存此原始消息，联网后自动补齐')}
 if(isApp&&chat.nativeProtocol){const value=await appCall('materialEnsure',{event:eid});return checked(eid,value).then(good=>{memory.set(eid,good);return good})}
 let result:any;for(let attempt=0;attempt<4;attempt++){try{result=(await requests.api('/api/materials/check',{messages:[{event_id:eid,payload_sha256:local?.meta.payload_sha256,sha256:local?.meta.sha256}]})).messages[0];break}catch(e:any){if(e.status!==503||attempt===3)throw e;await new Promise(r=>setTimeout(r,600*(attempt+1)))}}
 if(result.state==='expired'){if(local)return local;throw Error('服务器已清理此原始消息，请通过完整日期数据包恢复')}
 if(result.state==='ok'&&local)return save(eid,{...local,meta:result});
 const response=await requests.fetch(result.download);if(!response.ok)throw Error('原始消息暂不可用');const canonical=await response.text();const good=await checked(eid,{meta:result,canonical});const confirmed=await requests.api('/api/materials/'+encodeURIComponent(eid)+'/confirm',{payload_sha256:good.meta.payload_sha256,sha256:good.meta.sha256});if(confirmed.state!=='ok')throw Error('服务器未通过原始消息重新校验');return save(eid,{meta:confirmed,canonical});
 })().finally(()=>pending.delete(eid));pending.set(eid,task);return task}
window.addEventListener('yc:locked',()=>{memory.clear();pending.clear()});

window.addEventListener('yc:material-reset',()=>{memory.clear();window.dispatchEvent(new CustomEvent('yc:material',{detail:'*'}))});
