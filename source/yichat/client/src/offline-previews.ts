import {appCall,isApp} from './app';
import {requests,path} from './api';
export async function withOfflinePreview<T>(room:string,resource:string,load:()=>Promise<T>,offline=false):Promise<T>{
 const local=()=>appCall<T|null>('messagePreview',{room,resource});
 if(isApp&&(offline||!navigator.onLine)){
  const value=await local();if(value!=null)return value;
  throw Error('此消息尚未备份，联网后可查看');
 }
 try{return await load()}catch(e:any){
  if(isApp&&e?.name!=='AbortError'&&(!e.status||e.status>=500)){
   const value=await local().catch(()=>null);if(value!=null)return value;
  }
  throw e;
 }
}
export async function loadRecord(hash:string){
 if(isApp){const local=await appCall<any>('messagePreview',{room:'',resource:'record:'+hash}).catch(()=>null);if(local?.canonical){const {verifyRecord}=await import('./record-model');await verifyRecord(hash,local.canonical);return local}}
 return withOfflinePreview('', 'record:'+hash,()=>requests.api('/api/public-records/'+path(hash)));
}
