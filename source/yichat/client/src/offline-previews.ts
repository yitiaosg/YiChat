import {rememberFiles} from './file-descriptors';
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
 if(isApp){const local=await appCall<any>('messagePreview',{room:'',resource:'record:'+hash}).catch(()=>null);if(local?.canonical){const {verifyRecord}=await import('./record-model');await verifyRecord(hash,local.canonical);rememberFiles(local.files);return local}}
 const value:any=await withOfflinePreview('', 'record:'+hash,async()=>{for(let n=0;n<40;n++){const r=await requests.api('/api/public-records/'+path(hash));if(r.state!=='pending')return r;await new Promise(resolve=>setTimeout(resolve,3000))}throw Error('原会话暂时没有可提供完整备份的在线 App，请稍后重试')});
 const {verifyRecord}=await import('./record-model');await verifyRecord(hash,value.canonical);rememberFiles(value.files);
 if(isApp)void appCall('recordSave',{hash,...value}).catch(()=>{});return value;
}
