import {Component,type ComponentChildren} from 'preact';
import {chat} from './chat';import {localMaterialDocument} from './packet-display';
import {useEffect,useState} from 'preact/hooks';import {MessagesSquare} from 'lucide-preact';
import {RecordCard} from './sharing-ui';
import {readMaterial,ensureMaterial,type MaterialDocument} from './material-store';
import {ShareCard,OrderBubble} from './previews';import {Media} from './conversation';import {RecordTranscript,RecordOpenLink} from './record-view';import {ErrorText,Modal,TextLinks} from './shared';import {messageBody} from './message-display';
class MaterialGuard extends Component<{document:any;children:ComponentChildren},{failed:boolean}>{
 state={failed:false};componentDidCatch(){this.setState({failed:true})}
 componentDidUpdate(prior:{document:any}){if(prior.document!==this.props.document&&this.state.failed)this.setState({failed:false})}
 render(){return this.state.failed?<ErrorText>此条原始消息已损坏，无法显示。</ErrorText>:this.props.children}
}
export function MaterialBody({document:doc,room='',sender='',interactive=false}:{document:MaterialDocument;room?:string;sender?:string;interactive?:boolean}){
 const [open,setOpen]=useState(false);
 if(doc.kind==='media')return <Media content={doc.content} eager/>;
 if(doc.kind==='record')return <><div class="share-card record-card"><button class="share-content" onClick={()=>setOpen(true)}><strong><MessagesSquare size={16}/> 聊天记录 · {doc.record.payload.messages.length} 条</strong><small>已保存原始记录</small>{doc.record.payload.messages.slice(0,3).map((m:any)=><p>{m.name}：{m.content.body?.startsWith('https://')?'[原始分享消息]':String(m.content.body||'附件').slice(0,60)}</p>)}</button></div>{open&&<Modal title="聊天记录 · 原始数据" close={()=>setOpen(false)}><RecordOpenLink hash={doc.record.hash}/><RecordTranscript payload={doc.record.payload} publicHash={doc.record.hash}/></Modal>}</>;
 const urls=doc.previews.map(p=>p.url),body=doc.previews.some(p=>['gift','order'].includes(p.type))?'':messageBody(doc.content,urls);
 return <>{body&&<p class="message-body"><TextLinks body={body}/></p>}{doc.previews.map((p:any)=>p.type==='order'&&interactive?<OrderBubble key={p.url} initial={p.data} room={room} receiptSender={doc.content['org.yitiao.share']?.type==='receipt'?sender:undefined}/>:<ShareCard key={p.url} url={p.url} room={room} snapshot={{...p,original:true}}/>)}</>;
}
export function MaterialMessage({event,room,sender}:{event:string;room:string;sender:string}){
 const [value,setValue]=useState<MaterialDocument>(),[error,setError]=useState('');
 useEffect(()=>{if(chat.untrustedEntries.has(event))return;let active=true;const run=async()=>{try{const local=await readMaterial(event).catch(()=>undefined);if(active&&local){setValue(local.document);setError('')}const fresh=await ensureMaterial(event);if(active){setValue(fresh.document);setError('')}}catch(e:any){if(active)setError(e.message)}};void run();const changed=(e:Event)=>{if((e as CustomEvent).detail===event||(e as CustomEvent).detail==='*')void readMaterial(event).then(v=>{if(active&&v)setValue(v.document)}).catch(()=>{})};window.addEventListener('yc:material',changed);return()=>{active=false;window.removeEventListener('yc:material',changed)}},[event]);
 const untrusted=chat.untrustedEntries.get(event);let selected:MaterialDocument|undefined;try{if(untrusted?.material?.canonical)selected=localMaterialDocument(JSON.parse(untrusted.material.canonical),untrusted.files||[])}catch{}
 const display=untrusted?selected:value;const wire=chat.client?.getRoom(room)?.findEventById(event)?.getContent();const access=wire?.['org.yitiao.record_access'];
 return <div class="material-message" data-material-id={event}>{display?<MaterialGuard document={display}><MaterialBody document={display} room={room} sender={sender} interactive/></MaterialGuard>:untrusted?<ErrorText>所选数据包的此条原件已损坏，无法显示。</ErrorText>:access?<RecordCard content={{body:'https://chat.yitiao.org/records/'+access}} room={room}/>:<p class="muted" role="status">正在读取本机原始消息…</p>}{error&&!access&&<ErrorText>{error}</ErrorText>}</div>;
}
