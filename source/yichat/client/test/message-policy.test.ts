import test from 'node:test';import assert from 'node:assert/strict';
import {MESSAGE_EPOCH,chatStoreScope,clearLegacyChatStores} from '../src/message-policy';
test('chat reset isolates SDK sync and outbox caches by epoch',()=>{
 assert.notEqual(chatStoreScope('device'), 'device');
 assert.equal(chatStoreScope('device'),MESSAGE_EPOCH+':device');
 assert.notEqual(chatStoreScope('other'),chatStoreScope('device'));
});
test('legacy cache cleanup preserves wallet/business vault and new chats',async()=>{
 const removed:string[]=[];
 Object.defineProperty(globalThis,'indexedDB',{configurable:true,value:{
  databases:async()=>['yc-sync:old','yc-rust:old','yc-vault:old','yc-vault:account:uid:business','yc-sync:'+MESSAGE_EPOCH+':device','another-app'].map(name=>({name})),
  deleteDatabase(name:string){removed.push(name);const request:any={};queueMicrotask(()=>request.onsuccess());return request}
 }});
 await clearLegacyChatStores();assert.deepEqual(removed,['yc-sync:old','yc-rust:old','yc-vault:old']);
});
