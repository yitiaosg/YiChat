import * as sdk from 'matrix-js-sdk';
import {requests} from './api';
import type {Chat} from './chat';
export type Receipt={event_id:string;room:string;sequence:number;received_ms:number;type?:string;sender?:string;origin_server_ts:number;sha256:string;business?:string;redacts?:string;tombstone?:any};
export async function verifyPacket(raw:string,receipt:Pick<Receipt,'sha256'|'event_id'|'room'>){
 const bytes=Uint8Array.from(atob(raw),c=>c.charCodeAt(0)),digest=await crypto.subtle.digest('SHA-256',bytes),actual=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
 if(actual!==receipt.sha256)throw Error('历史数据包哈希校验失败，已拒绝接纳');
 const event=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));if(event.event_id!==receipt.event_id||event.room_id!==receipt.room)throw Error('历史数据包身份不匹配');return event;
}
// Web reconciles server recall metadata only. Native Apps own automatic
// message backup and peer recovery. Leave pre-existing packet stores intact.
export class History {
 receipts=new Map<string,Receipt>();status='正在检查撤回更新…';
 private redactions=new Map<string,any>();private timer?:ReturnType<typeof setInterval>;private busy=false;private stopped=false;private cursor=0;private roomCursors:Record<string,number>={};private first=true;private range?:{since:number;after:number;until:number};
 constructor(private owner:Chat){}
 async start(){this.cursor=Number(await this.owner.businessVault.get('historyCursor'))||0;this.roomCursors=await this.owner.businessVault.state('historyRooms')||{};if(this.stopped)return;void this.run();this.timer=setInterval(()=>{if(!document.hidden)void this.run()},20000);window.addEventListener('online',this.online);document.addEventListener('visibilitychange',this.visible)}
 private online=()=>{void this.run()};private visible=()=>{if(!document.hidden)void this.run()};
 async run(){if(this.busy||this.stopped)return;this.busy=true;try{
  if(!this.range)this.range={since:Math.max(Date.now()-31*86400000,Math.min(this.cursor,...[...this.owner.info.keys()].map(room=>this.roomCursors[room]||0))-(this.first?600000:300000)),after:0,until:0};
  for(let page=0;page<4&&this.range&&!this.stopped;page++){
   const r=this.range,response=await requests.api('/api/history/updates?since='+r.since+'&after='+r.after+'&until='+r.until);r.until=response.until;
   for(const receipt of response.records as Receipt[]){this.receipts.set(receipt.event_id,receipt);if(receipt.redacts)this.redactions.set(receipt.redacts,{event_id:receipt.event_id,room_id:receipt.room,type:'m.room.redaction',sender:receipt.sender,origin_server_ts:receipt.origin_server_ts,redacts:receipt.redacts,content:{redacts:receipt.redacts}});if(receipt.tombstone?.redaction)this.redactions.set(receipt.event_id,receipt.tombstone.redaction)}
   if(response.next_after!==null)r.after=response.next_after;else{this.cursor=response.until;for(const room of response.rooms)this.roomCursors[room]=this.cursor;await this.owner.businessVault.saveState('historyRooms',this.roomCursors);await this.owner.businessVault.set('historyCursor',this.cursor);this.range=undefined;this.first=false}
  }
  while(this.receipts.size>10000)this.receipts.delete(this.receipts.keys().next().value!);
  this.status=this.range?'正在检查更多撤回更新…':'撤回更新已核对 · '+new Date(this.cursor).toLocaleString();
 }catch(e:any){if(!this.stopped&&e.name!=='AbortError')this.status='撤回更新暂未完成：'+e.message}finally{this.busy=false;if(!this.stopped)this.owner.changed()}}
 timeline(room:sdk.Room){const events=room.getLiveTimeline().getEvents();for(const event of events){const raw=this.redactions.get(event.getId()!);if(raw&&!event.isRedacted())event.makeRedacted(new sdk.MatrixEvent(raw),room)}return events}
 stop(){this.stopped=true;clearInterval(this.timer);window.removeEventListener('online',this.online);document.removeEventListener('visibilitychange',this.visible);this.receipts.clear();this.redactions.clear()}
}
