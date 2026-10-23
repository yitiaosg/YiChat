// Focused editing-removal / preview regression. Real loopback Matrix and SDK; no production accounts/assets.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
const origin='http://127.0.0.1:18018';
const dist=resolve(import.meta.dirname,'../../client/dist');
const artifacts=resolve(process.env.YICHAT_ARTIFACTS||'/tmp/yichat-no-edit-evidence');await mkdir(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const checks=[],errors=[],wire=[];let contexts=[];
async function check(name,fn){if(process.env.YICHAT_ONLY&&!new RegExp(process.env.YICHAT_ONLY).test(name))return;console.log('CHECK',name);await fn();checks.push(name)}
async function setup(name,newDevice=false,initialRoom=''){
 const account=await(await fetch(origin+(newDevice?'/__fixture/new-device/':'/__fixture/accounts/')+name)).json();account.device_id=account.device;delete account.proof;
 const context=await browser.newContext({viewport:{width:412,height:915}});contexts.push(context);
 await context.route(origin+'/**',async route=>{const p=new URL(route.request().url()).pathname;if(initialRoom&&p==='/api/rooms')await new Promise(r=>setTimeout(r,2000));if(p==='/'||p.startsWith('/assets/')||p.startsWith('/render/')||p.startsWith('/brand/')){const file=resolve(dist,p==='/'?'index.html':'.'+p);const type={'.html':'text/html','.js':'application/javascript','.css':'text/css','.wasm':'application/wasm','.png':'image/png'}[extname(file)]||'application/octet-stream';return route.fulfill({body:await readFile(file),contentType:type})}return route.continue()});
 await context.addInitScript(({account,initialRoom})=>{if(initialRoom)localStorage.setItem('test:pending-room',initialRoom);
  window.__calls=[];window.YiChatApp={onmessage:null,async postMessage(message){const {id,method,args}=JSON.parse(message);window.__calls.push({method,args});let value=null;
   if(method==='session')value={session:account,capabilities:localStorage.getItem('test:legacy')==='1'?{}:{notifications:true},online:localStorage.getItem('test:offline')!=='1'};
   if(method==='backupStatus')value={packets:2,days:null,status:'内部加密备份已启用',exportStatus:'未启用外部导出'};
   if(method==='notificationStatus')value={enabled:window.__notificationEnabled!==false,permitted:true};if(method==='configureNotifications'){window.__notificationEnabled=args.enabled;value={enabled:args.enabled,permitted:true}};if(method==='pendingNotification'){value={room:localStorage.getItem('test:pending-room')||''};localStorage.removeItem('test:pending-room')};if(method==='configureExport')value={cancelled:true};if(method==='saveStart')value={transfer:'isolated-download'};if(method==='saveFinish')value={saved:true};
   setTimeout(()=>window.YiChatApp.onmessage?.({data:JSON.stringify({id,ok:true,value})}),0);
  }};sessionStorage.setItem('yc:backup-hint-seen','1');
 },{account,initialRoom});
 const page=await context.newPage();page.setDefaultTimeout(30000);page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().includes('/send/'))wire.push({name,path:new URL(r.url()).pathname,data:r.postDataJSON()})});
 await page.goto(origin);await page.locator('.sidebar').waitFor({timeout:70000,state:initialRoom?'attached':'visible'});return {page,context,account};
}
async function open(page,label){await page.locator('.room-row').filter({has:page.locator('strong',{hasText:new RegExp('^'+label+'$')})}).click();await page.getByRole('textbox',{name:'消息',exact:true}).waitFor()}
async function send(page,text){await page.getByRole('textbox',{name:'消息',exact:true}).fill(text);await page.getByRole('button',{name:'发送消息',exact:true}).click();await page.locator('.outgoing').waitFor({state:'detached',timeout:45000})}
async function api(account,path,data,method){for(let i=0;i<8;i++){const response=await fetch(origin+path,{method:method||(data===undefined?'GET':'POST'),headers:{Authorization:'Bearer '+account.token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data)}),body=await response.json();if(response.status===429){await new Promise(r=>setTimeout(r,body.retry_after_ms||1000));continue}return {status:response.status,body}}throw Error('rate limit persisted')}
try{
 const a=await setup('alice',true),b=await setup('bob',true);const ready=await(await fetch(origin+'/__fixture/ready')).json();
 await check('numeric zero preference flags never render as preview text, including after refresh',async()=>{
  assert.equal((await api(a.account,'/api/conversations/'+encodeURIComponent(ready.group)+'/preferences',{pinned:false,muted:false})).status,200);
  await a.page.reload();await a.page.locator('.sidebar').waitFor();const preview=a.page.locator('.room-row').filter({has:a.page.locator('strong',{hasText:/^Android isolated group$/})}).locator('.room-summary small');assert.ok(!/^(?:0\s+){1,2}/.test((await preview.innerText()).trim()));assert.equal(await preview.locator('svg').count(),0);
  await a.page.screenshot({path:artifacts+'/mobile-preview.png'});
 });
 await check('group send/reply/recall work, edit buttons do not exist',async()=>{
  await open(a.page,'Android isolated group');await open(b.page,'Android isolated group');await send(a.page,'这条消息只支持撤回');await b.page.locator('.message-body').filter({hasText:/^这条消息只支持撤回$/}).last().waitFor();
  for(const page of [a.page,b.page])assert.equal(await page.getByRole('button',{name:/编辑/}).count(),0);
  await a.page.bringToFront();const originalMessage=a.page.locator('.message.mine').filter({hasText:'这条消息只支持撤回'}).last();await originalMessage.getByRole('button',{name:'撤回消息',exact:true}).waitFor();const eventID=await originalMessage.getAttribute('data-event-id'),original=a.page.locator('[data-event-id="'+eventID+'"]');await original.getByRole('button',{name:'回复消息',exact:true}).click();await a.page.getByRole('button',{name:'取消回复',exact:true}).waitFor();await send(a.page,'回复仍然可用');await b.page.locator('.message-body').filter({hasText:/^回复仍然可用$/}).last().waitFor();assert.equal(await a.page.locator('.reply-strip').count(),0);
  a.page.once('dialog',d=>d.accept());await original.getByRole('button',{name:'撤回消息',exact:true}).click();await original.getByText('消息已撤回',{exact:true}).waitFor();await b.page.locator('.message .bubble > p').filter({hasText:/^消息已撤回$/}).last().waitFor();
  assert.equal(wire.filter(r=>r.data?.['m.relates_to']?.rel_type==='m.replace').length,0);const replies=wire.filter(r=>r.data?.['m.relates_to']?.['m.in_reply_to']);assert.equal(replies.length,1);await a.page.screenshot({path:artifacts+'/group-recall.png'});
 });
 await check('E2EE private messages keep recall and have no editing controls',async()=>{
  for(const page of [a.page,b.page])await page.getByRole('button',{name:'返回会话列表',exact:true}).click();await open(a.page,'bob');await send(a.page,'加密私聊没有编辑入口');await b.page.bringToFront();await open(b.page,'alice');await b.page.locator('.message-body').filter({hasText:/^加密私聊没有编辑入口$/}).last().waitFor();assert.equal(await a.page.getByRole('button',{name:/编辑/}).count(),0);assert.equal(await b.page.getByRole('button',{name:/编辑/}).count(),0);assert.equal(await b.page.locator('.message:not(.mine) button[aria-label="撤回消息"]').count(),0);const row=wire.find(r=>r.name==='alice'&&r.data?.ciphertext);assert.ok(row);assert.equal(JSON.stringify(row.data).includes('加密私聊没有编辑入口'),false);assert.ok(row.path.includes('/send/m.room.encrypted/'));await a.page.screenshot({path:artifacts+'/private-no-edit.png'});
 });
 await check('desktop preview flags show icons only when enabled and no numeric placeholders',async()=>{
  await a.page.getByRole('button',{name:'返回会话列表',exact:true}).click();await a.page.setViewportSize({width:1440,height:1000});await a.page.locator('.room-row').filter({has:a.page.locator('strong',{hasText:/^Android isolated group$/})}).locator('..').getByRole('button',{name:'置顶会话',exact:true}).click();await a.page.waitForFunction(()=>!document.querySelector('button[aria-label="取消置顶会话"]')?.disabled);
  const entry=a.page.locator('.room-row').filter({has:a.page.locator('strong',{hasText:/^Android isolated group$/})});await entry.locator('.room-summary small svg').waitFor();assert.equal((await entry.locator('.room-summary small').innerText()).trim(),'回复仍然可用');await a.page.screenshot({path:artifacts+'/desktop-preview.png'});
  await entry.locator('..').getByRole('button',{name:'取消置顶会话',exact:true}).click();await a.page.waitForFunction(()=>[...document.querySelectorAll('.conversation-entry')].find(e=>e.querySelector('strong')?.textContent==='Android isolated group')?.querySelector('button[aria-label="置顶会话"]')?.disabled===false);await a.page.reload();await a.page.locator('.sidebar').waitFor();assert.equal(await entry.locator('.room-summary small svg').count(),0);assert.equal((await entry.locator('.room-summary small').innerText()).trim(),'回复仍然可用');
 });
 assert.deepEqual(errors,[]);await writeFile(artifacts+'/result.json',JSON.stringify({checks,errors},null,2));console.log('PASS',checks.length);
}catch(e){for(let i=0;i<contexts.length;i++)for(const page of contexts[i].pages()){await page.screenshot({path:artifacts+'/failure-'+i+'.png'}).catch(()=>{});await writeFile(artifacts+'/failure-'+i+'.txt',(await page.locator('body').innerText()).slice(0,16000)).catch(()=>{})}console.error(e.stack||String(e));process.exitCode=1}
finally{await browser.close()}
