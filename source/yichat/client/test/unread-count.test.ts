import test from 'node:test';import assert from 'node:assert/strict';import {unreadCount} from '../src/unread-count';
const room=(id:string)=>({getUnreadNotificationCount:()=>5,getLiveTimeline:()=>({getEvents:()=>[{getId:()=>id,getWireType:()=> 'm.room.message'}]})});
test('server account read watermarks suppress stale SDK badges without hiding a newer message',()=>{
 const pref={read_sequence:20,latest_event:'$read',unread_count:0};assert.equal(unreadCount(room('$read'),pref),0);assert.equal(unreadCount(room('$new'),pref),5);assert.equal(unreadCount(room('$read')),5);
});
