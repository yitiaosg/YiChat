import {test} from 'node:test';
import assert from 'node:assert/strict';
import {selectionAllowed} from '../src/message-selection';
const now=1800000000000;
function event(age=600001,content:any={msgtype:'m.text',body:'缓存的旧消息'}){return {getTs:()=>now-age,getContent:()=>content,isRedacted:()=>false,isDecryptionFailure:()=>false}}
test('cached messages remain selectable after reopening without incremental receipts',()=>{assert.equal(selectionAllowed(event(),undefined,now),true);assert.equal(selectionAllowed(event(600001,{msgtype:'m.text',body:'https://chat.yitiao.org/orders/'+'a'.repeat(32)}),undefined,now),true)});
test('known receipt time remains authoritative and exactly five minutes is too recent',()=>{assert.equal(selectionAllowed(event(),{received_ms:now-300000},now),false);assert.equal(selectionAllowed(event(),{received_ms:now-300001},now),true);assert.equal(selectionAllowed(event(300000),undefined,now),false);assert.equal(selectionAllowed(event(300001),undefined,now),true)});
test('redacted, failed and invalid timestamps stay unavailable',()=>{for(const e of [{...event(),isRedacted:()=>true},{...event(),isDecryptionFailure:()=>true},{...event(),getTs:()=>NaN}])assert.equal(selectionAllowed(e,undefined,now),false)});

test('record links can be selected for ordinary forwarding',()=>assert.equal(selectionAllowed(event(600001,{body:'https://chat.yitiao.org/records/'+'a'.repeat(64)}),undefined,now),true));
