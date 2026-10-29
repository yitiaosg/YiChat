import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';
(globalThis as any).window={};
const {messageBody}=await import('../src/message-display');
const {notificationText}=await import('../src/notification-summary');
const card='https://yitiao.org/ycards/card/'+'a'.repeat(32)+'/CID/';
test('recognized previews hide the raw URL while preserving captions and other links',()=>{
 assert.equal(messageBody({body:card},[card]),'');
 assert.equal(messageBody({body:'看看这张卡片 '+card},[card]),'看看这张卡片');
 assert.equal(messageBody({body:card+' https://example.com/'},[card]),'https://example.com/');
 assert.equal(messageBody({body:card},[]),card);
 assert.equal(messageBody({body:card+'evil'},[card]),card+'evil');
});
test('native fetch and server FCM summaries use the same private/group wording',()=>{
 const fixtures=JSON.parse(readFileSync(new URL('../../tests/fixtures/notification-summary.json',import.meta.url),'utf8'));
 for(const row of fixtures)for(const kind of ['private','group'] as const)assert.equal(notificationText(row.content,kind),row[kind],row.name+':'+kind);
});
