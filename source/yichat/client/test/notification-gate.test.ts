import test from 'node:test';import assert from 'node:assert/strict';
import {ReplayedEvents,shouldNotify} from '../src/notification-gate';
const event=(id:string,sender='@bob:x',type='m.room.message')=>({getId:()=>id,getSender:()=>sender,getWireType:()=>type});
test('messages rebuilt from the local store never notify again',()=>{
 const replayed=new ReplayedEvents(),room={hasUserReadEvent:()=>false};
 assert.equal(shouldNotify(event('$new'),room,'@me:x',replayed),true);
 replayed.mark([event('$old1'),event('$old2')]);
 assert.equal(shouldNotify(event('$old1'),room,'@me:x',replayed),false);
 assert.equal(shouldNotify(event('$old2'),room,'@me:x',replayed),false);
 assert.equal(shouldNotify(event('$new'),room,'@me:x',replayed),true);
});
test('own, non-message, local-echo and already read events do not notify',()=>{
 const replayed=new ReplayedEvents();
 assert.equal(shouldNotify(event('$a','@me:x'),{},'@me:x',replayed),false);
 assert.equal(shouldNotify(event('$a','@bob:x','m.room.member'),{},'@me:x',replayed),false);
 assert.equal(shouldNotify(event('~local'),{},'@me:x',replayed),false);
 assert.equal(shouldNotify(event('$read'),{hasUserReadEvent:(_u:string,id:string)=>id==='$read'},'@me:x',replayed),false);
 assert.equal(shouldNotify(event('$unread'),{hasUserReadEvent:()=>{throw Error('unknown')}},'@me:x',replayed),true);
});
test('the replay record is bounded and keeps the most recent ids',()=>{
 const replayed=new ReplayedEvents(3);replayed.mark(['$1','$2','$3','$4'].map(id=>event(id)));
 assert.equal(replayed.has('$1'),false);assert.equal(replayed.has('$4'),true);
 replayed.mark([event('$2')]);replayed.mark([event('$5')]);assert.equal(replayed.has('$2'),true);assert.equal(replayed.has('$3'),false);
});
