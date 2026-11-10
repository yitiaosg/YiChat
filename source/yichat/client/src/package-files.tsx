import {useRef,useState} from 'preact/hooks';
import {Upload,FileArchive,X} from 'lucide-preact';
export type PackageFile={name:string;file:File;data:{days:Array<{room_id:string;complete?:boolean}>}};
function size(bytes:number){return bytes<1024?bytes+' B':bytes<1024*1024?(bytes/1024).toFixed(1)+' KB':(bytes/1024/1024).toFixed(1)+' MB'}
export function PackageFiles({files,busy,multiple,load,remove}:{files:PackageFile[];busy:boolean;multiple:boolean;load:(files:File[])=>void;remove:(index:number)=>void}){
 const input=useRef<HTMLInputElement>(null),[drag,setDrag]=useState(false);
 function choose(items:File[]){if(!busy&&items.length)load(multiple?items:items.slice(0,1))}
 return <><div class={'package-file-drop'+(drag?' dragging':'')} onDragOver={e=>{e.preventDefault();if(!busy)setDrag(true)}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDrag(false)}} onDrop={e=>{e.preventDefault();setDrag(false);choose(Array.from(e.dataTransfer?.files||[]))}}>
  <Upload size={26}/><strong>{multiple?'添加聊天记录数据包':'选择聊天记录数据包'}</strong><p>{multiple?'可分次添加多个 YCP 文件，合并或单独核验':'选择一个 YCP 文件，也可拖放到这里'}</p>
  <button type="button" disabled={busy} onClick={()=>input.current?.click()}>{files.length&&multiple?'继续添加数据包':'选择数据包'}</button>
  <input ref={input} class="package-file-input" aria-label="选择数据包文件" type="file" multiple={multiple} accept=".ycp,application/octet-stream" disabled={busy} onChange={e=>{choose(Array.from(e.currentTarget.files||[]));e.currentTarget.value=''}}/>
 </div>{files.length>0&&<ul class="package-file-list" aria-label="已选择的数据包">{files.map((item,index)=><li key={item.name+'|'+index}><FileArchive size={22}/><span><strong title={item.name}>{item.name}</strong><small>{size(item.file.size)} · {new Set(item.data.days.map(d=>d.room_id)).size} 个会话 · {item.data.days.length} 个日期 · {item.data.days.filter(d=>d.complete).length} 天完整</small></span><button type="button" class="icon" disabled={busy} aria-label={'移除 '+item.name} onClick={()=>remove(index)}><X size={18}/></button></li>)}</ul>}</>;
}
