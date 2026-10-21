// The canonical server archive uses ZIP_STORED. Read only, never extract to disk.
export function archiveEntries(bytes:Uint8Array){
 const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),decoder=new TextDecoder('utf-8',{fatal:true});
 let end=-1;for(let at=bytes.length-22;at>=Math.max(0,bytes.length-65557);at--)if(view.getUint32(at,true)===0x06054b50){end=at;break}
 if(end<0||view.getUint16(end+4,true)||view.getUint16(end+6,true))throw Error('请选择服务器的统一每日 ZIP 归档');
 const count=view.getUint16(end+10,true),entries=new Map<string,Uint8Array>();let at=view.getUint32(end+16,true);
 for(let index=0;index<count;index++){
  if(at+46>end||view.getUint32(at,true)!==0x02014b50)throw Error('ZIP 目录损坏');
  const flags=view.getUint16(at+8,true),method=view.getUint16(at+10,true),size=view.getUint32(at+24,true),nameLength=view.getUint16(at+28,true),extra=view.getUint16(at+30,true),comment=view.getUint16(at+32,true),offset=view.getUint32(at+42,true);
  const name=decoder.decode(bytes.subarray(at+46,at+46+nameLength));
  if(flags&1||method!==0||offset+30>at||view.getUint32(offset,true)!==0x04034b50||entries.has(name))throw Error('不支持此 ZIP 格式');
  const start=offset+30+view.getUint16(offset+26,true)+view.getUint16(offset+28,true);
  if(start+size>at||size>100000000)throw Error('ZIP 内容越界');
  if(['archive.json','events.ndjson'].includes(name))entries.set(name,bytes.subarray(start,start+size));else entries.set(name,new Uint8Array());
  at+=46+nameLength+extra+comment;
 }
 if(!entries.has('archive.json')||!entries.has('events.ndjson'))throw Error('缺少每日归档元数据或事件文件');return entries;
}
export async function hashFile(file:File){if(file.size>100000000)throw Error('本地核验单个文件上限为100MB；较大归档请在桌面校验');const bytes=new Uint8Array(await file.arrayBuffer());const digest=await crypto.subtle.digest('SHA-256',bytes);return {bytes,sha256:[...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('')}}
