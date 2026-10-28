import test from 'node:test';
import assert from 'node:assert/strict';
import {MatrixEvent} from 'matrix-js-sdk';
import type {Chat} from '../src/chat';
// The bridge is absent in Node. Tests exercise the shared SDK-facing boundary.
(globalThis as any).window={};
const {requests}=await import('../src/api');
requests.api=async()=>({events:['$event']}) as any;
const {notificationContent,notificationText}=await import('../src/notification-content');

function fixture(event:MatrixEvent,options:{failed?:boolean;muted?:boolean}={}){
 const room={findEventById:()=>event,getMyMembership:()=> 'join'};
 let fetched=0;
 const chat={phase:'ready',refresh:async()=>{},session:{mxid:'@receiver'},info:new Map([['!room',{kind:'direct'}]]),preferences:new Map([['!room',{muted:options.muted}]]),
  readOnly:()=>false,loadProfiles:async()=>{},name:()=> '本地联系人',
  client:{getRoom:()=>room,fetchRoomEvent:async()=>{fetched++;return event.event},getCrypto:()=>({}),decryptEventIfNeeded:async()=>{if(options.failed)throw Error('key missing')}}} as unknown as Chat;
 return {chat,fetched:()=>fetched};
}
test('verified plain group content becomes a notification message',async()=>{
 const event=new MatrixEvent({event_id:'$event',room_id:'!room',sender:'@sender',type:'m.room.message',origin_server_ts:123,content:{msgtype:'m.text',body:'正文'}});
 const {chat}=fixture(event);
 const messages=await notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'});
 assert.deepEqual(messages,[{id:'$event',sender:'本地联系人',body:'正文',time:123}]);
});
test('muted or read-only conversations are not fetched for previews',async()=>{
 const event=new MatrixEvent({event_id:'$event',room_id:'!room',type:'m.room.message',content:{body:'正文'}});
 const {chat,fetched}=fixture(event,{muted:true});
 await assert.rejects(notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'}));
 assert.equal(fetched(),0);
});
test('SDK-decrypted content is used without forwarding ciphertext',async()=>{
 const event=new MatrixEvent({event_id:'$event',room_id:'!room',sender:'@sender',type:'m.room.encrypted',origin_server_ts:123,content:{algorithm:'m.megolm.v1.aes-sha2',ciphertext:'must never display'}});
 const {chat}=fixture(event);
 chat.client.decryptEventIfNeeded=async()=>{await event.attemptDecryption({decryptEvent:async()=>({clearEvent:{type:'m.room.message',content:{msgtype:'m.text',body:'解密后的消息'}}})} as any)};
 const result=await notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'});
 assert.equal(result[0].body,'解密后的消息');assert.ok(!JSON.stringify(result).includes('ciphertext'));
});
test('network errors and mismatched event identity cannot produce a replacement',async()=>{
 const event=new MatrixEvent({event_id:'$other',room_id:'!room',sender:'@sender',type:'m.room.message',content:{body:'do not show'}});
 const {chat}=fixture(event);
 await assert.rejects(notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'}));
 chat.client.fetchRoomEvent=async()=>{throw Error('offline')};
 await assert.rejects(notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'}));
});
test('missing room keys or decryption timeout cannot produce notification text',async()=>{
 const event=new MatrixEvent({event_id:'$event',room_id:'!room',sender:'@sender',type:'m.room.encrypted',content:{algorithm:'m.megolm.v1.aes-sha2',ciphertext:'unavailable'}});
 const {chat}=fixture(event);
 chat.client.decryptEventIfNeeded=async()=>{await event.attemptDecryption({decryptEvent:async()=>{throw Error('synthetic missing key')}} as any)};
 const clock=Date.now;let calls=0;
 try {
  Date.now=()=>calls++===0?0:31_000;
  await assert.rejects(notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'}));
  assert.equal(event.isDecryptionFailure(),true);
 } finally {Date.now=clock}
});
test('attachments use labels without fetching or decrypting media',()=>{
 assert.equal(notificationText({msgtype:'m.image',body:'image.jpg'}),'[图片]');
 assert.equal(notificationText({msgtype:'m.file',body:'sensitive filename'}),'[文件]');
});
