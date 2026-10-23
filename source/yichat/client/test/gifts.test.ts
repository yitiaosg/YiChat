import test from 'node:test';
import assert from 'node:assert/strict';
import {giftId,giftMessage} from '../src/gifts';

const id='a'.repeat(32),instance='b'.repeat(32);
const operation={type:'gift',card:{name:'星空卡'},payload:{instance,recipient:'bob'},result:{id,instance,state:'transferred'}};

test('a completed gift stores only an owner-independent URL in the message',()=>{
 const message=giftMessage(operation);
 assert.deepEqual(message,{msgtype:'m.text',body:'https://chat.yitiao.org/gifts/'+id});
 assert.equal(giftId(message),id);
 assert.ok(!JSON.stringify(message).includes('bob'));
 assert.ok(!JSON.stringify(message).includes('星空卡'));
});

test('failed, incomplete or mismatched transfers cannot announce a completed gift',()=>{
 for(const candidate of [{...operation,result:undefined},{...operation,result:{...operation.result,state:'pending'}},{...operation,result:{...operation.result,instance:'c'.repeat(32)}}]){
  assert.throws(()=>giftMessage(candidate),/赠卡尚未完成/);
 }
});

test('gift bubbles recognize legacy shares and authoritative business references',()=>{
 assert.equal(giftId({'org.yitiao.share':{type:'gift',id}}),id);
 assert.equal(giftId({'org.yitiao.business':{kind:'gift',id}}),id);
 assert.equal(giftId({'org.yitiao.share':{type:'order',id}}),undefined);
 assert.equal(giftId({'org.yitiao.share':{type:'gift',id:'../other-room'}}),undefined);
 assert.equal(giftId({'org.yitiao.business':{kind:'gift',id},'org.yitiao.share':{type:'gift',id:'c'.repeat(32)}}),id);
});
