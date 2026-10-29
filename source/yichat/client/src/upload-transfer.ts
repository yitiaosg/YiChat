// Transfer progress is committed bytes on the server, never browser send-buffer progress.
export type TransferState={key:string;id?:string};
export type TransferReply={id:string;state:string;offset:number;bytes:number;chunk_bytes:number;url?:string;expires?:number};
export type TransferCall=(path:string,method:string,body?:unknown,headers?:Record<string,string>)=>Promise<TransferReply>;
export async function transferFile(file:Blob,room:string,state:TransferState,call:TransferCall,signal:AbortSignal,changed:(offset:number,phase:string)=>void,wait:(ms:number)=>Promise<void>){
 let confirmed=0;
 const check=(r:TransferReply)=>{
  if(!r||!/^[a-f0-9]{48}$/.test(r.id)||state.id&&r.id!==state.id||r.bytes!==file.size||!Number.isSafeInteger(r.offset)||r.offset<confirmed||r.offset>file.size||!Number.isSafeInteger(r.chunk_bytes)||r.chunk_bytes<1||r.chunk_bytes>131072||!['uploading','finalizing','complete'].includes(r.state))throw Error('服务器上传确认数据不一致，已停止发送');
  if(r.state==='complete'&&(r.url!=='mxc://chat.yitiao.org/'+r.id||r.offset!==file.size))throw Error('服务器文件确认不完整');
  return r;
 };
 const aborted=()=>{if(signal.aborted)throw new DOMException('已取消','AbortError')};
 const retry=async(fn:()=>Promise<TransferReply>)=>{
  for(let attempt=0;;attempt++){
   aborted();try{return check(await fn())}catch(e:any){
    aborted();if(e.status&&e.status<500&&![408,409,429].includes(e.status)||attempt>=3)throw e;
    changed(confirmed,'retrying');await wait(500*2**attempt);
   }
  }
 };
 let current:TransferReply;
 if(state.id)current=check(await retry(()=>call('/api/uploads/'+state.id,'GET')));
 else {current=await retry(()=>call('/api/uploads','POST',{room,bytes:file.size,key:state.key}));state.id=current.id}
 confirmed=current.offset;changed(confirmed,'uploading');
 const recover=async(error:unknown)=>{
  const r=check(await call('/api/uploads/'+state.id,'GET'));
  if(r.offset>confirmed||r.state==='complete')return r;
  throw error;
 };
 while(current.offset<file.size){
  aborted();const offset=current.offset,chunk=file.slice(offset,offset+current.chunk_bytes);
  current=await retry(async()=>{try{return await call('/api/uploads/'+state.id,'PUT',chunk,{'X-Upload-Offset':String(offset)})}catch(e:any){if(e.status&&e.status<500&&e.status!==408&&e.status!==409&&e.status!==429)throw e;return recover(e)}});
  if(current.offset<=offset)throw Error('服务器未确认新的分块，请重试上传');
  confirmed=current.offset;changed(confirmed,'uploading');
 }
 if(current.state!=='complete'){
  changed(confirmed,'confirming');
  current=await retry(async()=>{try{return await call('/api/uploads/'+state.id+'/complete','POST',{})}catch(e:any){if(e.status&&e.status<500&&![408,409,429].includes(e.status))throw e;return recover(e)}});
 }
 if(current.state!=='complete')throw Error('文件尚未确认完成，请重试');
 return current;
}
