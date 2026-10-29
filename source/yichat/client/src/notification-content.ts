import * as sdk from 'matrix-js-sdk';
import type {Chat} from './chat';
import {appCall} from './app';
import {requests,path,sleep} from './api';

export type NotificationRequest={id:string;room:string;kind:'private'|'group';event:string};
export function notificationText(content:Record<string,any>):string {
 const media:Record<string,string>={'m.image':'[图片]','m.audio':'[语音]','m.video':'[视频]','m.file':'[文件]'};
 return (media[content.msgtype]||content.body||'').slice(0,2000);
}
// Only a verified MatrixEvent's clear content reaches the native notification.
// The push is an untrusted hint; membership, preferences and messages are fetched
// from the authenticated gateway. No crypto primitives or exported session keys.
export async function notificationContent(chat:Chat,request:NotificationRequest){
 if(!request.room.startsWith('!')||request.room.length>255||!request.event.startsWith('$')||request.event.length>255)throw Error('Invalid hint');
 const deadline=Date.now()+30_000;
 while(chat.phase==='loading'&&Date.now()<deadline)await sleep(100);
 if(chat.phase!=='ready')throw Error('Client unavailable');
 await chat.refresh();
 const info=chat.info.get(request.room),preference=chat.preferences.get(request.room);
 if(!info||chat.readOnly(request.room)||preference?.muted||preference?.archived||preference?.deleted||(info.kind==='direct'?'private':'group')!==request.kind)throw Error('Conversation unavailable');
 const room=chat.client.getRoom(request.room)!;
 const unread=await requests.api<{events:string[]}>('/api/notifications/'+path(request.room)+'/events?through='+path(request.event));
 if(!unread.events.length||unread.events.length>20||!unread.events.includes(request.event))throw Error('No unread notification');
 const messages=[];
 for(const id of unread.events){
 const raw=await chat.client.fetchRoomEvent(request.room,id);
 if(raw.event_id!==id||raw.room_id!==request.room)throw Error('Event mismatch');
 const event=room.findEventById(id)||new sdk.MatrixEvent(raw);
 if(event.isRedacted()||event.getSender()===chat.session.mxid)throw Error('Event unavailable');
 if(event.isEncrypted())throw Error('Retired encrypted event');
 if(event.isRedacted()||event.getType()!=='m.room.message')throw Error('Event unavailable');
 const body=notificationText(event.getContent());if(!body)throw Error('Content unavailable');
 await chat.loadProfiles([event.getSender()!]);
 messages.push({id,sender:chat.name(event.getSender()),body,time:event.getTs()});
 }
 return messages;
}
export function installNotificationContent(chat:Chat){
 window.addEventListener('yc:notification-sync',()=>chat.client?.retryImmediately());
 window.addEventListener('yc:notification-fetch',((event:CustomEvent<NotificationRequest>)=>{
  const request=event.detail;
  void notificationContent(chat,request)
   .then(messages=>appCall('notificationResult',{id:request.id,ok:true,messages}))
   .catch(()=>appCall('notificationResult',{id:request.id,ok:false}).catch(()=>{}));
 }) as EventListener);
}
