import {transferFile,type TransferState} from './upload-transfer';
import { requests, path, ApiError, retryDelay, sleep } from './api';
import { chat } from './chat';
import {isApp,appSave} from './app';
export type Upload={id:string;file:File;progress:number;state:string;error?:string;abort:AbortController;content?:any;attachment?:string;room:string;transfer?:TransferState};
let tail:Promise<any>=Promise.resolve();
export function uploadFile(upload:Upload,changed:()=>void){
  const task=tail.then(async()=>{
    const {file,room,abort}=upload;upload.state='preparing';upload.error=undefined;changed();
    if(file.size>chat.session.max_file)throw Error('文件超过 100 MB（100000000 字节）');
    if(chat.info.get(room)?.encryption!=='plain')throw Error('旧会话已停用，请重新打开会话');
    upload.transfer??={key:crypto.randomUUID()};
    const call=async(endpoint:string,method:string,body?:unknown,headers:Record<string,string>={})=>{
      const control=new AbortController(),cancel=()=>control.abort();abort.signal.addEventListener('abort',cancel,{once:true});if(abort.signal.aborted)control.abort();
      let expired=false;const timer=setTimeout(()=>{expired=true;control.abort()},30000);
      try{
        const binary=body instanceof Blob;
        const response=await requests.fetch(endpoint,{method,signal:control.signal,headers:{...headers,...(body===undefined?{}:{'Content-Type':binary?'application/octet-stream':'application/json'})},body:body===undefined?undefined:binary?body:JSON.stringify(body)});
        const data=await response.json().catch(()=>null);if(!response.ok)throw new ApiError(response.status,data||{});return data;
      }catch(e:any){if(expired&&!abort.signal.aborted)throw Error('上传请求超时，已确认的进度已保留，可点击重试');throw e}
      finally{clearTimeout(timer);abort.signal.removeEventListener('abort',cancel)}
    };
    let result;
    try{result=await transferFile(file,room,upload.transfer,call,abort.signal,(offset,state)=>{upload.progress=file.size?offset/file.size:1;upload.state=state;changed()},ms=>sleep(ms,abort.signal))}
    catch(e:any){
      if(e.status===410){upload.transfer=undefined;upload.progress=0}
      if(e.name==='AbortError'&&upload.transfer?.id)void requests.fetch('/api/uploads/'+upload.transfer.id,{method:'DELETE'}).catch(()=>{});
      if(e instanceof TypeError)throw Error('上传连接中断，已确认的进度已保留，可点击重试');
      throw e;
    }
    const msgtype=file.type.startsWith('image/')?'m.image':file.type.startsWith('audio/')?'m.audio':file.type.startsWith('video/')?'m.video':'m.file';
    upload.content={msgtype,body:file.name,info:{mimetype:file.type||'application/octet-stream',size:file.size},url:result.url};
    upload.attachment=result.id;upload.state='ready';upload.progress=1;changed();
  }).catch(e=>{upload.state=e.name==='AbortError'?'cancelled':'failed';upload.error=e.message;changed()});
  tail=task;return task;
}
const urls=new Set<string>();
export function releaseUrl(url:string){URL.revokeObjectURL(url);urls.delete(url)}
export function blobUrl(blob:Blob){const url=URL.createObjectURL(blob);urls.add(url);return url}
window.addEventListener('yc:locked',()=>{for(const url of urls)URL.revokeObjectURL(url);urls.clear()});
export async function attachment(content:any,signal:AbortSignal){
  if(content.file)throw Error('旧加密附件已清空，请使用新会话中的附件');
  const mxc=content.url;
  const match=/^mxc:\/\/chat\.yitiao\.org\/([a-f0-9]{48})$/.exec(mxc||'');if(!match)throw Error('附件地址不受支持');
  let response:Response|undefined;
  for(let attempt=0;attempt<5;attempt++){
    response=await requests.fetch('/api/files/'+match[1]+'/content',{signal});if(response.ok)break;
    const data=await response.json();
    if(response.status===503&&data.code==='YICHAT_HISTORY_PENDING'&&attempt<4){await sleep(retryDelay(response,data)+attempt*200,signal);continue}
    if(response.status===410)throw Error('文件已过期');if(response.status===503)throw Error('文件暂不可用，服务异常；可稍后重试');throw new ApiError(response.status,data)
  }
  if(!response?.ok)throw Error('文件暂不可用，请稍后重试');
  const bytes=await response.arrayBuffer();
  // Never execute HTML/SVG or arbitrary uploaded files in the app origin.
  const type=String(content.info?.mimetype||'application/octet-stream');
  return new Blob([bytes],{type:/^(image\/(png|jpeg|gif|webp|avif)|audio\/[a-z0-9.+-]+|video\/[a-z0-9.+-]+)$/i.test(type)?type:'application/octet-stream'});
}
export async function saveBlob(blob:Blob,name:string){if(isApp){await appSave(blob,name);return}const url=blobUrl(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>releaseUrl(url),30000)}
