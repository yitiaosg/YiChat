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
test('cards and media omit the outer bubble; plain text and disabled previews retain it',async()=>{
 const {standaloneContent}=await import('../src/message-display');
 for(const msgtype of ['m.image','m.audio','m.video','m.file'])assert.equal(standaloneContent({msgtype}),true);
 assert.equal(standaloneContent({msgtype:'m.text'},[card]),true);assert.equal(standaloneContent({msgtype:'m.text'},[],true),true);
 assert.equal(standaloneContent({msgtype:'m.text',body:card,'org.yitiao.no_preview':true}),false);assert.equal(standaloneContent({msgtype:'m.text',body:'hello'}),false);
});

test('transfer and red-packet previews are distinct, including old App envelopes',async()=>{const {messageCaption}=await import('../src/message-display');const old={msgtype:'m.text',body:'转账 / 红包','org.yitiao.material_type':'order','org.yitiao.business':{kind:'order',id:'a'.repeat(32)}};assert.equal(messageCaption(old,'direct'),'转账');assert.equal(messageCaption(old,'group'),'红包');assert.equal(messageCaption({...old,'org.yitiao.order_kind':'direct'},'group'),'转账');assert.equal(messageCaption({...old,'org.yitiao.order_kind':'group'},'direct'),'红包');assert.equal(messageCaption({...old,'org.yitiao.material_type':'receipt'},'group'),'[已收款]');assert.equal(messageCaption({msgtype:'m.text',body:'普通新消息'},'direct'),'普通新消息')});
