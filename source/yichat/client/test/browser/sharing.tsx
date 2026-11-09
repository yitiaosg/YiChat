import {render} from 'preact';
import {useState} from 'preact/hooks';
import {Conversation,Details} from '../../src/conversation';
import {RecordCard} from '../../src/sharing-ui';
import {chat,sdk} from '../../src/chat';
import {requests} from '../../src/api';
import '../../src/style.css';
const state=window as any;state.testSent=[];state.testProfileLoads=[];state.testApiCalls=[];state.testPolicy=true;state.testDelay=0;state.testEligiblePending=0;state.testPublications={};
const bob='@bob:chat.yitiao.org',alice='@alice:chat.yitiao.org';
const order={type:'order',url:'https://chat.yitiao.org/orders/'+'a'.repeat(32),data:{id:'a'.repeat(32),kind:'direct',amount:'12.50',title:'午饭',state:'paid',actions:{}}};
const make=(id:string,content:any,age=600001,sender=bob)=>new sdk.MatrixEvent({event_id:id,room_id:'!source',type:'m.room.message',sender,origin_server_ts:Date.now()-age,content});
const texts=['这是一整条消息，包含两行文字。\n第二行有 emoji 👋，长按应全选。','第二条普通消息'];
const items:any[]=texts.map((body,i)=>({id:'$text'+i,sender:i?alice:bob,username:i?'alice':'bob',name:i?'爱丽丝':'小鲍',avatar_url:'/api/avatars/bob?v=1',sent_at:Date.now()-600001,content:{msgtype:'m.text',body},previews:[]}));
items.push({id:'$special',sender:bob,username:'bob',name:'小鲍',avatar_url:'/api/avatars/bob?v=1',sent_at:Date.now()-600001,content:{msgtype:'m.text',body:order.url},previews:[order]} as any);
items.push({...items[2],id:'$receipt',previews:[{...order,receipt:true}]},{...items[2],id:'$packet',previews:[{...order,data:{...order.data,kind:'group',count:2,title:'午饭红包'}}]},{id:'$photo',sender:alice,username:'alice',name:'爱丽丝',avatar_url:'/api/avatars/alice?v=1',sent_at:Date.now()-600001,content:{msgtype:'m.image',body:'photo.png',url:'mxc://chat.yitiao.org/'+'a'.repeat(48),info:{mimetype:'image/png'}},previews:[]});
const canonical=JSON.stringify({version:2,owner:alice,identity:'both',messages:items}),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical)))).map(n=>n.toString(16).padStart(2,'0')).join('');
const record={'msgtype':'m.text',body:'https://chat.yitiao.org/records/'+hash,'org.yitiao.record':hash,'org.yitiao.record_json':canonical};
const events=[...items.map(item=>make(item.id,item.previews.length?{...item.content,'org.yitiao.forward_snapshot':item}:item.content,600001,item.sender)),make('$deleted',{msgtype:'m.text',body:'已经从服务器删除'}),make('$recent',{msgtype:'m.text',body:'刚发送的新消息'},10000),make('$record',record)];
const room:any={roomId:'!source',name:'测试会话',getMyMembership:()=> 'join',getJoinedMembers:()=>[{userId:alice},{userId:bob}],getUsersReadUpTo:()=>[],getMembers:()=>[{userId:alice,membership:'join'},{userId:bob,membership:'join'}],getMember:()=>({powerLevel:0}),currentState:{getStateEvents:()=>undefined},loadMembersIfNeeded:async()=>{},hasUserReadEvent:()=>false};
Object.assign(chat,{session:{uid:'test',mxid:alice,user:'alice'},rooms:[room,{roomId:'!target'}],info:new Map([['!source',{room:'!source',kind:'direct',owner:alice,peer:bob}]]),profiles:new Map(),preferences:new Map(),client:{getRoom:()=>room,getUser:()=>undefined,sendReadReceipt:async()=>{}},businessVault:{image:async()=>undefined,saveImage:async()=>{}},history:{receipts:new Map(),status:'服务器记录已校验',run:()=>{}},outbox:[],offline:false,sync:'已连接'});
chat.timeline=()=>events;chat.readOnly=()=>false;chat.canRedact=()=>false;chat.roomName=()=> '测试会话';chat.findEvent=(_,id)=>events.find(e=>e.getId()===id);chat.preferenceSaving=()=>false;
chat.loadProfiles=async(ids)=>{state.testProfileLoads.push(ids);chat.profiles.set(bob,{mxid:bob,username:'bob',nickname:'小鲍',avatar_url:'/api/avatars/bob?v=1'} as any);chat.profiles.set(alice,{mxid:alice,username:'alice',nickname:'爱丽丝'} as any);chat.changed()};
chat.setPreference=async(id,p)=>{chat.preferences.set(id,{...chat.preferences.get(id),...p});chat.changed()};
const mockRequests=requests as unknown as {fetch:()=>Promise<Response>;api:(url:string,data?:any,method?:string)=>Promise<any>};
mockRequests.fetch=async()=>{const c=document.createElement('canvas');c.width=c.height=64;const ctx=c.getContext('2d')!;ctx.fillStyle='#246caa';ctx.fillRect(0,0,64,64);ctx.fillStyle='white';ctx.font='bold 40px sans-serif';ctx.textAlign='center';ctx.fillText('B',32,47);return new Response(await new Promise<Blob>(resolve=>c.toBlob(b=>resolve(b!),'image/png')),{headers:{'Content-Type':'image/png'}})};
const publications=new Map<string,any>();
async function publication(ids:string[],identity='both',commit=true,active=true){
 const key=JSON.stringify([ids,identity]);if(publications.has(key)){const old=publications.get(key);if(active&&commit)old.active=true;return commit?old:{payload:old.payload,messages:old.messages,expires:old.expires}}
 const payload={version:2,owner:alice,identity,messages:items.filter(item=>ids.includes(item.id))};if(!commit)return {payload,messages:payload.messages,expires:Date.now()/1000+7*86400};const canonical=JSON.stringify(payload),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical)))).map(n=>n.toString(16).padStart(2,'0')).join('');
 const value={hash,canonical,payload,active,url:'https://chat.yitiao.org/records/'+hash,expires:Date.now()/1000+7*86400,messages:payload.messages};publications.set(key,value);state.testPublications[hash]=value;return value;
}
await publication(items.map(item=>item.id));
mockRequests.api=async(url,data,method)=>{
 state.testApiCalls.push({url,data,method});if(url.includes('/eligible')){state.testEligiblePending++;await new Promise(r=>setTimeout(r,state.testDelay));state.testEligiblePending--;if(url.includes('deleted'))throw Object.assign(Error('所选消息已从服务器删除'),{status:410});return {eligible:true}}
 if(url.includes('/api/public-records/')){const h=url.split('/').pop()!;if(state.testPublications[h]?.active)return state.testPublications[h];throw Object.assign(Error('引用消息已删除'),{status:410})}
 if(url.includes('/selection')){const ids=data.ids as string[],mode=data.mode;if(mode==='individual')return {messages:items.filter(item=>ids.includes(item.id)).map(item=>({...item.content,'org.yitiao.attachments':item.content.url?['a'.repeat(48)]:[]}))};const p=await publication(ids,data.identity,mode!=='prepare',mode==='image');return mode==='combined'?{...p,content:{msgtype:'m.text',body:p.url,'org.yitiao.record':p.hash,'org.yitiao.record_json':p.canonical,'org.yitiao.attachments':ids.includes('$photo')?['a'.repeat(48)]:[]}}:p}
 if(method==='PUT'){state.testPolicy=!data.blocked;return {allowed:state.testPolicy,can_set:true,blocked_by_me:!state.testPolicy}}
 return {allowed:state.testPolicy,can_set:true,blocked_by_me:!state.testPolicy};
};
chat.send=async(room,content)=>{state.testSent.push({room,content});const h=content['org.yitiao.record'];if(h&&state.testPublications[h])state.testPublications[h].active=true};
function Harness(){const [,refresh]=useState(0),[details,setDetails]=useState(false);chat.changed=()=>refresh(x=>x+1);return <main class="app-shell has-active"><Conversation roomId="!source" back={()=>{}} details={()=>setDetails(true)} business={()=>{}} security={()=>{}}/>{details&&<Details roomId="!source" close={()=>setDetails(false)} archive={()=>{}} onLeave={()=>{}} audit={()=>{}}/>}</main>}
render(<Harness/>,document.getElementById('app')!);
