import test from 'node:test';
import assert from 'node:assert/strict';
import {MatrixEvent} from 'matrix-js-sdk';
import type {MatrixClient} from 'matrix-js-sdk';
import type {CryptoBackend} from 'matrix-js-sdk/lib/common-crypto/CryptoBackend';
import {retryFailedDecryptions} from '../src/decryption';

test('a failed Matrix event is really retried after its room key arrives',async()=>{
 const event=new MatrixEvent({event_id:'$offline',room_id:'!room',type:'m.room.encrypted',content:{algorithm:'m.megolm.v1.aes-sha2',session_id:'session',ciphertext:'fixture'}});
 let available=false,calls=0;
 const crypto={decryptEvent:async()=>{calls++;if(!available)throw Error('room key not yet available');return {clearEvent:{type:'m.room.message',content:{msgtype:'m.text',body:'decrypted fixture'}}}}} as unknown as CryptoBackend;
 await event.attemptDecryption(crypto);
 assert.equal(event.isDecryptionFailure(),true);
 assert.equal(event.shouldAttemptDecryption(),false,'normal decryptEventIfNeeded cannot retry this failure');
 available=true;
 const client={getCrypto:()=>crypto,getRooms:()=>[{getLiveTimeline:()=>({getEvents:()=>[event]})}]} as unknown as MatrixClient;
 const result=await retryFailedDecryptions(client);
 assert.deepEqual(result,{retried:1,recovered:1,failed:0});
 assert.equal(event.isDecryptionFailure(),false);
 assert.equal(event.getContent().body,'decrypted fixture');
 assert.equal(calls,2);
 assert.deepEqual(await retryFailedDecryptions(client),{retried:0,recovered:0,failed:0});
});
