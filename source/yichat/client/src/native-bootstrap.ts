import * as sdk from 'matrix-js-sdk';import {appCall,isApp} from './app';import {verifyPacket} from './history';import {gmtDay,type DayPacket} from './packet-protocol';import type {Chat} from './chat';
export async function nativeBootstrap(chat:Chat){
 if(!isApp||!chat.nativeProtocol||!chat.client||!chat.session)return;
 const data=await appCall('protocolBootstrap');const ui=data.ui||{},roomNames=new Map<string,string>(ui.room_names||[]);chat.untrustedEntries=new Map((data.untrusted||[]).map((e:any)=>[e.event_id,e]));
 for(const [key,value] of ui.info||[])if(chat.offline||!chat.info.has(key))chat.info.set(key,value);
 for(const [key,value] of ui.profiles||[])if(chat.offline||!chat.profiles.has(key))chat.profiles.set(key,value);
 for(const [key,value] of ui.preferences||[])if(chat.offline||!chat.preferences.has(key))chat.preferences.set(key,value);
 for(const state of data.states||[])chat.packetStates.set(state.room_id+'|'+state.day,state.state);
 for(const saved of data.rooms||[]){
  const events:sdk.MatrixEvent[]=[];
  for(const row of saved.rows||[])try{const raw=await verifyPacket(row.raw,{sha256:row.sha256,event_id:row.event_id,room:saved.room});raw.unsigned={...raw.unsigned,'org.yitiao.received_ms':row.received_ms};events.push(new sdk.MatrixEvent(raw))}catch{/* A corrupt native packet is handled by full-day repair. */}
  if(!chat.info.has(saved.room))chat.info.set(saved.room,{room:saved.room,kind:'group',owner:chat.session.mxid,peer:null,encryption:'plain'});
  let room=chat.client.getRoom(saved.room);
  if(!room){room=new sdk.Room(saved.room,chat.client,chat.session.mxid,{pendingEventOrdering:sdk.PendingEventOrdering.Detached});const info=chat.info.get(saved.room)!;const members=[...new Set([chat.session.mxid,info.owner,...(info.peer?[info.peer]:[]),...events.map(e=>e.getSender()!)])];room.currentState.setStateEvents(members.map((mxid,i)=>new sdk.MatrixEvent({event_id:'$local-member-'+i,room_id:saved.room,type:'m.room.member',state_key:mxid,sender:mxid,content:{membership:'join',displayname:chat.name(mxid)}})));room.updateMyMembership('join' as any);if(roomNames.get(saved.room)||(info as any).name)room.currentState.setStateEvents([new sdk.MatrixEvent({event_id:'$local-name',room_id:saved.room,type:'m.room.name',state_key:'',sender:chat.session.mxid,content:{name:roomNames.get(saved.room)||(info as any).name}})]);room.recalculate();chat.client.store.storeRoom(room)}
  if(!room.getLiveTimeline().getEvents().length&&events.length)await room.addLiveEvents(events.reverse(),{addToState:false});
 }
 chat.updateRooms();
}
export async function replaceNativeDay(chat:Chat,roomId:string,day:string){
 const packet=await appCall<DayPacket>('protocolDay',{room:roomId,day,include_data:false});let room=chat.client.getRoom(roomId);if(!room){await nativeBootstrap(chat);room=chat.client.getRoom(roomId)}if(!room)return;
 for(const [id,e] of chat.untrustedEntries)if(e.room_id===roomId&&gmtDay(Number(e.received_ms))===day)chat.untrustedEntries.delete(id);if((packet as any).manual){const display=await appCall('protocolBootstrap');for(const e of display.untrusted||[])chat.untrustedEntries.set(e.event_id,e)}
 const prior=room.getLiveTimeline().getEvents().filter(e=>gmtDay(Number(e.getUnsigned()['org.yitiao.received_ms']||e.getTs()))!==day);
 const corrected=packet.entries.map(e=>{const raw=JSON.parse(e.raw);raw.unsigned={...raw.unsigned,'org.yitiao.received_ms':e.received_ms};if(e.redacted)raw.unsigned.redacted_because={type:'m.room.redaction',content:{redacts:e.event_id}};return new sdk.MatrixEvent(raw)});
 room.resetLiveTimeline(null,null);await room.addLiveEvents([...prior,...corrected].sort((a,b)=>Number(a.getUnsigned()['org.yitiao.received_ms']||a.getTs())-Number(b.getUnsigned()['org.yitiao.received_ms']||b.getTs())),{addToState:false});chat.packetStates.set(roomId+'|'+day,packet.state||'ok');chat.changed();window.dispatchEvent(new Event('yc:material-reset'));
}
