import {useEffect,useRef,useState} from 'preact/hooks';
import {appCall} from './app';import {chat} from './chat';import {requests} from './api';
import {archiveEntries,hashFile} from './archive-file';
import {verifyPacket} from './history';import {Modal,ErrorText,TextLinks} from './shared';
const FORMAT='yichat-record-digests-v1';
const labels:Record<string,string>={ok:'核验通过',mismatch:'不一致',incomplete:'原包缺失或损坏',not_sealed:'尚未封存',forbidden:'完整日权限不足',quarantined:'存证异常，已停止核验',unavailable:'此核验规格不可用'};
const yesterday=()=>new Date(Date.now()-86400000).toISOString().slice(0,10);
type Row={id:string;at:number;withdrawn:boolean;raw:any;content?:any;failed?:boolean};
export function RecordAudit({initial='',close}:{initial?:string;close:()=>void}){
 const [room,setRoom]=useState(initial),[rooms,setRooms]=useState<string[]>([]),[start,setStart]=useState(yesterday()),[end,setEnd]=useState(yesterday()),[results,setResults]=useState<any[]>([]),[day,setDay]=useState(''),[rows,setRows]=useState<Row[]>([]),[next,setNext]=useState<string|null>(null),[cursors,setCursors]=useState(['0']),[key,setKey]=useState(''),[unlocked,setUnlocked]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const disposed=useRef(false),generation=useRef(0),fileRows=useRef(new Map<string,Row[]>());
 const [source,setSource]=useState('internal'),[files,setFiles]=useState<File[]>([]);
 useEffect(()=>{void appCall<string[]>('recordRooms').then(ids=>{if(disposed.current)return;const list=[...new Set([...(initial?[initial]:[]),...ids,...chat.rooms.map(r=>r.roomId)])];setRooms(list);if(!initial)setRoom(list[0]||'')}).catch(e=>setError(e.message));return()=>{disposed.current=true;generation.current++}},[]);
 const span=(Date.parse(end+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))/86400000+1;
 const privateRoom=['direct','legacy_encrypted'].includes(chat.info.get(room)?.encryption||'')||rows.some(r=>r.raw.type==='m.room.encrypted');
 function reset(){fileRows.current.clear();setResults([]);setRows([]);setDay('');setKey('');setUnlocked(false);setError('');generation.current++}
 async function renderPage(values:Row[],_decrypt:boolean){
  return values.map(row=>({...row,content:row.raw.content||{},failed:row.raw.type==='m.room.encrypted'}));
 }
 async function page(selected:string,after='0',history=['0'],decrypt=unlocked){
  const id=generation.current;
  if(source==='files'){const all=fileRows.current.get(selected)||[],offset=Number(after),values=all.slice(offset,offset+30),rendered=await renderPage(values,decrypt);if(disposed.current||id!==generation.current)return;setRows(rendered);setDay(selected);setCursors(history);setNext(offset+30<all.length?String(offset+30):null);return}
  const result=await appCall('recordPage',{room,day:selected,after}),values:Row[]=[];
  for(const item of result.rows){const raw=await verifyPacket(item.raw,{event_id:item.event_id,room,sha256:item.sha256});values.push({id:item.event_id,at:item.received_ms,withdrawn:item.withdrawn,raw})}
  const rendered=await renderPage(values,decrypt);if(disposed.current||id!==generation.current)return;
  setRows(rendered);setDay(selected);setCursors(history);setNext(result.next_after||null);
 }
 async function run(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn()}catch(e:any){if(!disposed.current)setError(e.message)}finally{if(!disposed.current)setBusy(false)}}
 async function verify(){
  reset();if(source==='files'){await verifyFiles();return}const local=await appCall('recordDays',{room,start,end});
  if(local.format!==FORMAT||local.room!==room||local.start!==start||local.end!==end||local.days.length!==span)throw Error('本地备份返回的日期范围不一致');
  const days=local.days.map((d:any)=>({day:d.day,count:d.count,complete:d.complete,sha256:d.sha256}));
  const result=await requests.api('/api/records/verify',{room,start,end,days});
  if(result.format!==FORMAT||result.room!==room||result.days.length!==days.length)throw Error('服务器返回的核验范围不一致');
  for(let i=0;i<days.length;i++){const r=result.days[i],d=days[i];if(r.day!==d.day||(r.state==='ok'&&(!d.complete||r.sha256!==d.sha256||r.server_count!==d.count)))throw Error('核验结果与本地摘要不一致')}
  if(disposed.current)return;setResults(result.days);const first=result.days.find((r:any)=>r.state==='ok');if(first)await page(first.day,'0',['0'],false);
 }
 async function verifyFiles(){
  if(files.length!==span)throw Error('请为所选日期每天选择一个归档文件');
  const days:any[]=[],loaded=new Map<string,Uint8Array>();
  for(const file of files){const {bytes,sha256}=await hashFile(file),entries=archiveEntries(bytes),header=JSON.parse(new TextDecoder().decode(entries.get('archive.json')));
   if(header.format!=='yichat-archive'||header.version!==1||header.permission_spec!=='joined-whole-utc-day-v1'||header.room_id!==room||header.day<start||header.day>end||loaded.has(header.day)||!['events-manifest','with-attachments'].includes(header.spec))throw Error('文件不属于所选会话、连续日期或归档规格');
   days.push({day:header.day,spec:header.spec,bytes:file.size,sha256});loaded.set(header.day,entries.get('events.ndjson')!);
  }
  days.sort((a,b)=>a.day.localeCompare(b.day));const result=await requests.api('/api/records/verify-files',{room,start,end,days});
  if(result.format!=='yichat-archive-v1'||result.room!==room||result.days.length!==days.length)throw Error('归档核验范围不一致');
  for(let i=0;i<days.length;i++){
   const r=result.days[i],d=days[i];if(r.day!==d.day||(r.state==='ok'&&(r.sha256!==d.sha256||r.bytes!==d.bytes)))throw Error('归档核验结果与本地文件不一致');
   if(r.state!=='ok')continue;
   const bytes=loaded.get(d.day)!;if(bytes.length>20000000)throw Error('归档已核验，但事件文件超过20MB，请在桌面查看');
   const records:Row[]=[],withdrawn=new Set<string>();
   for(const line of new TextDecoder('utf-8',{fatal:true}).decode(bytes).split('\n')){if(!line)continue;const v=JSON.parse(line),raw=v.event;
    if(v.room_id!==room||raw.room_id!==room||raw.event_id!==v.event_id||!Number.isSafeInteger(v.received_ms)||new Date(v.received_ms).toISOString().slice(0,10)!==d.day)throw Error('归档事件元数据不一致');
    if(raw.type==='m.room.redaction'){const target=raw.content?.redacts||raw.redacts;if(target)withdrawn.add(target)}
    if(['m.room.message','m.room.encrypted','m.room.redaction'].includes(raw.type))records.push({id:v.event_id,at:v.received_ms,withdrawn:false,raw});
   }
   for(const r of records)r.withdrawn=withdrawn.has(r.id);fileRows.current.set(d.day,records);
  }
  if(disposed.current)return;setResults(result.days);const first=result.days.find((r:any)=>r.state==='ok');if(first)await page(first.day,'0',['0'],false);
 }
 return <Modal title="App 聊天记录核验" close={close}><p>从内部自动备份重新计算原消息包摘要，与服务器保存的存证核对。仅提交日期和摘要，正文、文件和密钥不会上传。</p><label>核验来源<select aria-label="核验来源" value={source} disabled={busy} onChange={e=>{reset();setFiles([]);setSource(e.currentTarget.value)}}><option value="internal">App 内部自动备份原包</option><option value="files">选择每日统一 ZIP 归档文件</option></select></label><label>备份会话<select aria-label="备份会话" value={room} disabled={busy} onChange={e=>{reset();setRoom(e.currentTarget.value)}}>{rooms.map(id=><option key={id} value={id}>{chat.client.getRoom(id)?chat.roomName(chat.client.getRoom(id)!):id}</option>)}</select></label>{source==='files'&&<label>选择归档文件（连续1至5日）<input type="file" multiple accept=".zip,application/zip" disabled={busy} onChange={e=>{reset();setFiles([...e.currentTarget.files||[]])}}/></label>}{!rooms.length&&<p class="notice">尚无本地原包备份。联网同步后再试。</p>}<div class="audit-dates"><label>开始日期（UTC）<input type="date" value={start} disabled={busy} onInput={e=>{reset();setStart(e.currentTarget.value)}}/></label><label>结束日期（UTC）<input type="date" value={end} disabled={busy} onInput={e=>{reset();setEnd(e.currentTarget.value)}}/></label></div><p class="small muted">必须连续，最多5日。当天未封存时不能作为整日核验通过。文件模式支持两种统一每日 ZIP 归档规格，不导入或覆盖本地数据。</p><button type="button" class="primary" disabled={busy||!room||!Number.isInteger(span)||span<1||span>5} onClick={()=>void run(verify)}>核验所选日期</button>
 {results.length>0&&<div class="audit-results">{results.map(r=><div key={r.day} class={r.state==='ok'?'notice':'confirm'}><strong>{r.day} · {labels[r.state]||'核验未完成'}</strong>{Number.isInteger(r.server_count)&&<p class="small">服务器存证 {r.server_count} 份原包</p>}{r.error&&<p class="small">{r.error}</p>}{r.state==='ok'&&<button type="button" disabled={busy} onClick={()=>void run(()=>page(r.day))}>查看 {r.day} 记录</button>}</div>)}</div>}
 {day&&<section class="audit-reader"><h3>{day} · 已核验记录（只读）</h3><p class="small muted">按客户端时区显示；已撤回消息保留提示，编辑另作记录，不导入或重发到实时会话。</p>
 {rows.map(row=>{const sealed=row.raw.type==='m.room.encrypted',withdrawn=row.withdrawn||chat.client.getRoom(room)?.findEventById(row.id)?.isRedacted()||!!chat.history.receipts.get(row.id)?.tombstone,content=row.content||{},edit=content['m.relates_to']?.rel_type==='m.replace',text=edit?content['m.new_content']?.body:content.body;return <article key={row.id} class="audit-record"><small>{chat.name(row.raw.sender)} · {new Date(row.at).toLocaleString()}</small>{sealed?<p class="muted">此旧加密记录已停用，请使用清理后生成的新聊天备份。</p>:row.failed?<p class="error">无法解密此原消息包，备份可能缺少该会话的历史密钥。</p>:withdrawn?<p class="muted">消息已撤回</p>:row.raw.type==='m.room.redaction'?<p class="muted">撤回记录</p>:<><p class="small muted">{edit?'编辑记录':content['m.relates_to']?.['m.in_reply_to']?'回复记录':''}</p><p class="message-body"><TextLinks body={typeof text==='string'?text:'此记录没有可显示的文字'}/></p>{['m.image','m.file','m.audio','m.video'].includes(content.msgtype)&&<small>附件记录；本页不自动下载附件。</small>}</>}</article>})}{!rows.length&&<p class="muted">此日期没有聊天消息。</p>}<div class="actions"><button type="button" disabled={busy||cursors.length<2} onClick={()=>void run(()=>{const prior=cursors.slice(0,-1);return page(day,prior.at(-1)!,prior)})}>上一页记录</button><button type="button" disabled={busy||!next} onClick={()=>void run(()=>page(day,next!,[...cursors,next!]))}>下一页记录</button></div></section>}
 {busy&&<p role="status">正在核验或读取本地备份…</p>}{error&&<ErrorText>{error}</ErrorText>}<p class="small muted">不一致、缺包、权限不足的日期不展示为可信记录；恢复密钥不能补回不存在的消息包。普通实时消息不会因此标为已核验。</p></Modal>;
}
