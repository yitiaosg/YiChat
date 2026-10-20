import { encryptAttachment, decryptAttachment } from 'matrix-encrypt-attachment';
import { requests, path, ApiError, retryDelay, sleep } from './api';
import { chat } from './chat';
export type Upload={id:string;file:File;progress:number;state:string;error?:string;abort:AbortController;content?:any;attachment?:string;room:string};
let tail:Promise<any>=Promise.resolve();
export function uploadFile(upload:Upload,changed:()=>void){
  const task=tail.then(async()=>{
    const {file,room,abort}=upload;upload.state='encrypting';upload.error=undefined;changed();
    if(file.size>chat.session.max_file)throw Error('文件超过 100 MB（100000000 字节）');
    let bytes=await file.arrayBuffer(),info:any;
    if(chat.info.get(room)?.encryption!=='plain'){const encrypted=await encryptAttachment(bytes);bytes=encrypted.data;info=encrypted.info}
    if(bytes.byteLength>chat.session.max_file)throw Error('实际上传的密文超过 100 MB');
    const blob=new Blob([bytes]);upload.state='uploading';changed();
    let result:any;
    for(let attempt=0;attempt<5;attempt++){
      await requests.slot(abort.signal);if(abort.signal.aborted)throw new DOMException('已取消','AbortError');
      try{result=await new Promise((resolve,reject)=>{
        const xhr=new XMLHttpRequest();xhr.open('POST','/api/files/'+path(room));xhr.setRequestHeader('Authorization','Bearer '+requests.token);xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.responseType='json';
        const cancel=()=>xhr.abort();abort.signal.addEventListener('abort',cancel,{once:true});
        xhr.upload.onprogress=e=>{upload.progress=e.lengthComputable?e.loaded/e.total:0;changed()};
        xhr.onload=()=>{abort.signal.removeEventListener('abort',cancel);if(xhr.status===401){requests.onUnauthorized();reject(Error('请重新登录'));return}if(xhr.status>=200&&xhr.status<300)resolve(xhr.response);else reject(Object.assign(new ApiError(xhr.status,xhr.response||{}),{retry:xhr.getResponseHeader('Retry-After')}))};
        xhr.onerror=()=>reject(Error('上传连接中断'));xhr.onabort=()=>reject(new DOMException('已取消','AbortError'));xhr.send(blob);
      });break}catch(e:any){if(e.status!==429||attempt===4)throw e;const response=new Response('',{status:429,headers:e.retry?{'Retry-After':e.retry}:{}});upload.state='限流等待';changed();await sleep(retryDelay(response,e.data)+Math.random()*250,abort.signal)}
    }
    const msgtype=file.type.startsWith('image/')?'m.image':file.type.startsWith('audio/')?'m.audio':file.type.startsWith('video/')?'m.video':'m.file';
    upload.content={msgtype,body:file.name,info:{mimetype:file.type||'application/octet-stream',size:file.size},...(info?{file:{...info,url:result.url}}:{url:result.url})};
    upload.attachment=result.id;upload.state='ready';upload.progress=1;changed();
  }).catch(e=>{upload.state=e.name==='AbortError'?'cancelled':'failed';upload.error=e.message;changed()});
  tail=task;return task;
}
const urls=new Set<string>();
export function releaseUrl(url:string){URL.revokeObjectURL(url);urls.delete(url)}
export function blobUrl(blob:Blob){const url=URL.createObjectURL(blob);urls.add(url);return url}
window.addEventListener('yc:locked',()=>{for(const url of urls)URL.revokeObjectURL(url);urls.clear()});
export async function attachment(content:any,signal:AbortSignal){
  const mxc=content.file?.url||content.url;
  const match=/^mxc:\/\/chat\.yitiao\.org\/([a-f0-9]{48})$/.exec(mxc||'');if(!match)throw Error('附件地址不受支持');
  let response:Response|undefined;
  for(let attempt=0;attempt<5;attempt++){
    response=await requests.fetch('/api/files/'+match[1]+'/content',{signal});if(response.ok)break;
    const data=await response.json();
    if(response.status===503&&data.code==='YICHAT_HISTORY_PENDING'&&attempt<4){await sleep(retryDelay(response,data)+attempt*200,signal);continue}
    if(response.status===410)throw Error('文件已过期');if(response.status===503)throw Error('文件暂不可用，服务异常；可稍后重试');throw new ApiError(response.status,data)
  }
  if(!response?.ok)throw Error('文件暂不可用，请稍后重试');
  let bytes=await response.arrayBuffer();if(content.file)bytes=await decryptAttachment(bytes,content.file);
  // Never execute HTML/SVG or arbitrary uploaded files in the app origin.
  const type=String(content.info?.mimetype||'application/octet-stream');
  return new Blob([bytes],{type:/^(image\/(png|jpeg|gif|webp|avif)|audio\/[a-z0-9.+-]+|video\/[a-z0-9.+-]+)$/i.test(type)?type:'application/octet-stream'});
}
export function saveBlob(blob:Blob,name:string){const url=blobUrl(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>releaseUrl(url),30000)}
