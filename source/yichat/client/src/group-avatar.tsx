import {useEffect,useRef,useState} from 'preact/hooks';
import {ImagePlus,Trash2} from 'lucide-preact';
import {chat} from './chat';
import {requests,path,ApiError} from './api';
import {ErrorText} from './shared';

export function GroupAvatarEditor({room,disabled,changed}:{room:string;disabled:boolean;changed:()=>Promise<void>}){
 const [blob,setBlob]=useState<Blob>(),[preview,setPreview]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const input=useRef<HTMLInputElement>(null),pending=useRef<{key:string;image?:string;remove:boolean}>(),controller=useRef<AbortController>();
 useEffect(()=>()=>controller.current?.abort(),[]);
 useEffect(()=>{if(!blob){setPreview('');return}const url=URL.createObjectURL(blob);setPreview(url);return()=>URL.revokeObjectURL(url)},[blob]);
 async function choose(file?:File){
  if(!file)return;setError('');pending.current=undefined;setBlob(undefined);
  if(file.size>20000000){setError('请选择20MB以内的图片');return}
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)){setError('请选择 PNG、JPEG 或 WebP 图片');return}
  setBusy(true);
  try{const image=await createImageBitmap(file);try{const size=Math.min(image.width,image.height),canvas=document.createElement('canvas');canvas.width=canvas.height=256;const context=canvas.getContext('2d');if(!context)throw Error('浏览器无法处理图片');context.drawImage(image,(image.width-size)/2,(image.height-size)/2,size,size,0,0,256,256);const result=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('图片处理失败')),'image/png'));setBlob(result)}finally{image.close()}}
  catch(e:any){setError(e.message||'图片无法读取，请更换文件')}
  finally{setBusy(false)}
 }
 async function save(remove=false){
  if(!remove&&!blob)return;setBusy(true);setError('');controller.current=new AbortController();
  let op=pending.current;if(!op||op.remove!==remove)pending.current=op={key:crypto.randomUUID(),remove};
  try{
   if(!remove&&!op.image){const response=await requests.fetch('/api/groups/'+path(room)+'/avatar',{method:'POST',headers:{'Content-Type':'image/png'},body:blob,signal:controller.current.signal});const data=await response.json();if(!response.ok)throw new ApiError(response.status,data);op.image=data.id}
   await requests.api('/api/groups/'+path(room)+'/manage',{action:'avatar',key:op.key,image:remove?null:op.image},undefined,controller.current.signal);
   pending.current=undefined;setBlob(undefined);await changed();chat.changed();
  }catch(e:any){if(e.name!=='AbortError'){setError(e.message);await changed().catch(()=>{})}}
  finally{setBusy(false)}
 }
 const current=chat.client.getRoom(room)?.currentState.getStateEvents('m.room.avatar','')?.getContent().url;
 return <section class="group-avatar-editor"><h3>群头像</h3><p class="small muted">仅群主可设置；图片会裁为正方形，群成员可见。</p><input ref={input} type="file" hidden accept="image/png,image/jpeg,image/webp" aria-label="选择群头像文件" onChange={e=>{const file=e.currentTarget.files?.[0];e.currentTarget.value='';void choose(file)}}/>{preview&&<img src={preview} alt="待保存群头像"/>}<div class="actions"><button type="button" disabled={disabled||busy} onClick={()=>input.current?.click()}><ImagePlus size={16}/>选择群头像</button>{blob&&<><button type="button" class="primary" disabled={disabled||busy} onClick={()=>void save()}>保存群头像</button><button type="button" disabled={busy} onClick={()=>{setBlob(undefined);pending.current=undefined;setError('')}}>取消选择</button></>}{current&&<button type="button" disabled={disabled||busy} onClick={()=>void save(true)}><Trash2 size={16}/>移除群头像</button>}</div>{busy&&<p class="small" role="status">正在处理并保存群头像…</p>}{error&&<ErrorText>{error}</ErrorText>}</section>;
}
