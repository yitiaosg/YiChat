import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifyPacket} from '../src/history';
test('peer packets require trusted digest and matching room and event identity',async()=>{
 const body=JSON.stringify({event_id:'$event',room_id:'!room',type:'m.room.encrypted',content:{ciphertext:'native ciphertext'}})+'\n',raw=Buffer.from(body).toString('base64'),receipt={event_id:'$event',room:'!room',sha256:createHash('sha256').update(body).digest('hex')};
 assert.equal((await verifyPacket(raw,receipt)).content.ciphertext,'native ciphertext');
 await assert.rejects(()=>verifyPacket(Buffer.from(body+' ').toString('base64'),receipt),/哈希校验失败/);
 await assert.rejects(()=>verifyPacket(raw,{...receipt,room:'!another-room'}),/身份不匹配/);
});
