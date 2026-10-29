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
test('verified plain private content becomes a notification message',async()=>{
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
test('retired ciphertext is rejected without invoking crypto or replacing placeholder',async()=>{
 const event=new MatrixEvent({event_id:'$event',room_id:'!room',sender:'@sender',type:'m.room.encrypted',content:{algorithm:'m.megolm.v1.aes-sha2',ciphertext:'must never display'}});
 const {chat}=fixture(event);let cryptoCalled=false;
 chat.client.decryptEventIfNeeded=async()=>{cryptoCalled=true};
 await assert.rejects(notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'}),/Retired/);
 assert.equal(cryptoCalled,false);
});
test('network errors and mismatched event identity cannot produce a replacement',async()=>{
 const event=new MatrixEvent({event_id:'$other',room_id:'!room',sender:'@sender',type:'m.room.message',content:{body:'do not show'}});
 const {chat}=fixture(event);
 await assert.rejects(notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'}));
 chat.client.fetchRoomEvent=async()=>{throw Error('offline')};
 await assert.rejects(notificationContent(chat,{id:'work',room:'!room',kind:'private',event:'$event'}));
});
test('attachments use labels without fetching or decrypting media',()=>{
 assert.equal(notificationText({msgtype:'m.image',body:'image.jpg'}),'对方发送了一张图片');
 assert.equal(notificationText({msgtype:'m.file',body:'sensitive filename'}),'对方发送了一个文件');
});
