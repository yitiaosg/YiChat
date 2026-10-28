// Run against tests/android_fixture.py and Vite :5173. Only synthetic accounts.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
const gateway='http://127.0.0.1:18018',origin='http://127.0.0.1:5173';
const accounts={};for(const user of ['alice','bob'])accounts[user]=await(await fetch(gateway+'/__fixture/new-device/'+user)).json();
const rooms=await(await fetch(gateway+'/__fixture/ready')).json();
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const contexts=[];
async function setup(user){
 const context=await browser.newContext();contexts.push(context);
 await context.addInitScript(({account,room})=>{
  window.__noticeResult=null;window.__noticeRequest=null;
  window.YiChatApp={onmessage:null,postMessage(raw){const message=JSON.parse(raw);let value=null;
   if(message.method==='session')value={session:{...account,device_id:account.device},online:true};
   if(message.method==='notificationRequest')value=window.__noticeRequest;
   if(message.method==='notificationResult')window.__noticeResult=message.args;
   queueMicrotask(()=>window.YiChatApp.onmessage?.({data:JSON.stringify({id:message.id,ok:true,value})}));
  }};
 },{account:accounts[user],room:rooms.direct});
 await context.route('**/*',route=>{
  const url=new URL(route.request().url());
  if(url.origin===origin&&/^\/(api|_matrix|auth)\//.test(url.pathname))return route.continue({headers:{...route.request().headers(),origin:gateway}});
  return route.continue();
 });
 console.log('Starting synthetic client',user);
 const page=await context.newPage();page.on('response',async response=>{if(response.status()===401)console.log('Unauthorized synthetic request',new URL(response.url()).pathname)});page.on('pageerror',error=>console.log('Test page error:',error.message));await page.goto(origin+'/test/browser/notification.html');
 await page.waitForFunction(()=>['ready','error','login'].includes(window.__notificationTestChat?.phase),{},{timeout:90_000}).catch(async error=>{console.log(await page.evaluate(()=>({phase:window.__notificationTestChat?.phase,error:window.__notificationTestChat?.error})));throw error});
 const state=await page.evaluate(()=>({phase:window.__notificationTestChat?.phase,error:window.__notificationTestChat?.error}));assert.equal(state.phase,'ready',JSON.stringify(state));
 console.log('Synthetic client ready',user);return {context,page};
}
async function send(page,text){
 const before=await(await fetch(gateway+'/api/notifications',{headers:{Authorization:'Bearer '+accounts.bob.token}})).json();
 await page.evaluate(async({room,text})=>{const chat=window.__notificationTestChat;await chat.refresh();
  if(chat.client.getRoom(room)?.getMyMembership()==='invite')await chat.join(room);
  await chat.send(room,{msgtype:'m.text',body:text});
  if(chat.outbox.length)throw Error('Synthetic message send failed');
 },{room:rooms.direct,text});
 for(let i=0;i<120;i++){
  const result=await(await fetch(gateway+'/api/notifications?after='+before.cursor,{headers:{Authorization:'Bearer '+accounts.bob.token}})).json();
  const row=result.rooms?.find(row=>row.room===rooms.direct);if(row)return row.event;
  await new Promise(resolve=>setTimeout(resolve,500));
 }
 throw Error('Synthetic accepted event unavailable');
}

try{
 const a=await setup('alice'),b=await setup('bob');
 // Accept the room and exchange the initial device keys before removing UI.
 await b.page.evaluate(async room=>{const c=window.__notificationTestChat;await c.refresh();if(c.client.getRoom(room)?.getMyMembership()==='invite')await c.join(room)},rooms.direct);
 await send(a.page,'initial synthetic encrypted exchange');
 await b.page.waitForFunction(room=>window.__notificationTestChat.client.getRoom(room)?.getLiveTimeline().getEvents().some(e=>e.getContent().body==='initial synthetic encrypted exchange'),rooms.direct,{timeout:60_000});
 await b.page.evaluate(async room=>{
  const chat=window.__notificationTestChat,event=chat.client.getRoom(room).getLiveTimeline().getEvents().findLast(e=>e.getContent().body==='initial synthetic encrypted exchange');
  await chat.client.sendReadReceipt(event);await chat.setPreference(room,{read:true,event:event.getId()});
 },rooms.direct);
 console.log('Initial synthetic E2EE verified');
 await b.page.close();
 await a.page.evaluate(room=>window.__notificationTestChat.client.getCrypto().forceDiscardSession(room),rooms.direct);
 const event=await send(a.page,'synthetic offline notification ciphertext');
 const worker=await b.context.newPage();
 await worker.addInitScript(request=>{window.__noticeRequest=request},{id:'isolated-background-job',room:rooms.direct,kind:'private',event});
 await worker.goto(origin+'/notification.html');
 await worker.waitForFunction(()=>window.__noticeResult!==null,{},{timeout:60_000});
 const result=await worker.evaluate(()=>window.__noticeResult);
 assert.equal(result.ok,true);assert.equal(result.messages[0].id,event);assert.equal(result.messages[0].body,'synthetic offline notification ciphertext');
 await worker.close();
 const reopened=await b.context.newPage();await reopened.goto(origin+'/test/browser/notification.html');
 await reopened.waitForFunction(()=>['ready','error','login'].includes(window.__notificationTestChat?.phase),{},{timeout:90_000});
 await reopened.waitForFunction(room=>window.__notificationTestChat.client.getRoom(room)?.getLiveTimeline().getEvents().some(e=>e.getContent().body==='synthetic offline notification ciphertext'),rooms.direct,{timeout:60_000});
 console.log(JSON.stringify({passed:['initial E2EE','headless same-device offline new Megolm session decrypts','UI reopens with intact keys and sync cache']}));
}finally{await browser.close()}
