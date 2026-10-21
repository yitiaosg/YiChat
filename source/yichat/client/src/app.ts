type Port={postMessage(message:string):void;onmessage:((event:{data:string})=>void)|null};
const port=(window as Window & {YiChatApp?:Port}).YiChatApp;
export const isApp=!!port;
const pending=new Map<string,{resolve:(value:any)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
let serial=0;
if(port){
 document.documentElement.dataset.app='android';
 port.onmessage=event=>{try{const result=JSON.parse(event.data),task=pending.get(result.id);if(!task)return;pending.delete(result.id);clearTimeout(task.timer);if(result.ok)task.resolve(result.value);else task.reject(Error(result.error||'App 操作未完成'))}catch{/* Ignore malformed/unrelated responses. */}};
}
export function appCall<T=any>(method:string,args:Record<string,unknown>={}):Promise<T>{
 if(!port)return Promise.reject(Error('此功能仅在 YiChat App 中提供'));
 return new Promise((resolve,reject)=>{const id=String(++serial),timer=setTimeout(()=>{pending.delete(id);reject(Error('App 操作已超时，请重试'))},600000);pending.set(id,{resolve,reject,timer});port.postMessage(JSON.stringify({id,method,args}))});
}
export async function appSave(blob:Blob,name:string){
 const {transfer}=await appCall('saveStart',{name,mime:blob.type||'application/octet-stream',bytes:blob.size});
 try{
  for(let offset=0;offset<blob.size;offset+=49152){const bytes=new Uint8Array(await blob.slice(offset,offset+49152).arrayBuffer());const data=btoa(String.fromCharCode(...bytes));await appCall('saveChunk',{transfer,data})}
  const result=await appCall('saveFinish',{transfer});if(result?.cancelled)throw new DOMException('已取消保存','AbortError');
 }catch(e){await appCall('saveCancel',{transfer}).catch(()=>{});throw e}
}
