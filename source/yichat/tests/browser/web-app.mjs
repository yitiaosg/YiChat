// Focused wrapper regression. Loopback isolated Synapse only; no production accounts/assets.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
const origin='http://127.0.0.1:18018';
const dist=resolve(import.meta.dirname,'../../client/dist');
const artifacts=resolve(process.env.YICHAT_ARTIFACTS||'/tmp/yichat-web-app-artifacts');await mkdir(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const checks=[],errors=[],wire=[];let contexts=[];
async function check(name,fn){console.log('CHECK',name);await fn();checks.push(name)}
async function setup(name,newDevice=false){
 const account=await(await fetch(origin+(newDevice?'/__fixture/new-device/':'/__fixture/accounts/')+name)).json();account.device_id=account.device;delete account.proof;
 const context=await browser.newContext({viewport:{width:412,height:915}});contexts.push(context);
 await context.route(origin+'/**',async route=>{const p=new URL(route.request().url()).pathname;if(p==='/'||p.startsWith('/assets/')||p.startsWith('/render/')||p.startsWith('/brand/')){const file=resolve(dist,p==='/'?'index.html':'.'+p);const type={'.html':'text/html','.js':'application/javascript','.css':'text/css','.wasm':'application/wasm','.png':'image/png'}[extname(file)]||'application/octet-stream';return route.fulfill({body:await readFile(file),contentType:type})}return route.continue()});
 await context.addInitScript(({account})=>{
  window.__calls=[];window.YiChatApp={onmessage:null,postMessage(message){const {id,method,args}=JSON.parse(message);window.__calls.push({method,args});let value=null;
   if(method==='session')value={session:account,online:localStorage.getItem('test:offline')!=='1'};
   if(method==='backupStatus')value={packets:2,days:null,status:'内部加密备份已启用',exportStatus:'未启用外部导出'};
   if(method==='configureExport')value={cancelled:true};if(method==='saveStart')value={transfer:'isolated-download'};if(method==='saveFinish')value={saved:true};
   setTimeout(()=>window.YiChatApp.onmessage?.({data:JSON.stringify({id,ok:true,value})}),0);
  }};sessionStorage.setItem('yc:backup-hint-seen','1');
 },{account});
 const page=await context.newPage();page.setDefaultTimeout(30000);page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.url().includes('/send/'))wire.push({name,path:new URL(r.url()).pathname,data:r.postDataJSON()})});
 await page.goto(origin);await page.locator('.sidebar').waitFor({timeout:70000});return {page,context};
}
async function open(page,label){await page.locator('.room-row').filter({has:page.locator('strong',{hasText:new RegExp('^'+label+'$')})}).click();await page.getByRole('textbox',{name:'消息',exact:true}).waitFor()}
async function send(page,text){await page.getByRole('textbox',{name:'消息',exact:true}).fill(text);await page.getByRole('button',{name:'发送消息',exact:true}).click();await page.locator('.outgoing').waitFor({state:'detached',timeout:45000})}
try{
 const a=await setup('alice'),b=await setup('bob');
 await check('shared mobile frontend and restricted framework selection',async()=>{assert.equal(await a.page.locator('html').getAttribute('data-app'),'android');assert.equal(await a.page.locator('button[aria-label="发起会话"]').first().evaluate(e=>getComputedStyle(e).userSelect),'none');assert.ok(await a.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))});
 await check('two isolated accounts synchronize and exchange encrypted private message',async()=>{await open(a.page,'bob');assert.equal(await b.page.locator('.room-row').filter({has:b.page.locator('strong',{hasText:/^alice$/})}).count(),0);await send(a.page,'网页 App 基础验证：私聊端到端加密。');await open(b.page,'alice');await b.page.locator('.message-body').filter({hasText:'网页 App 基础验证：私聊端到端加密。'}).waitFor({timeout:45000});assert.equal(await b.page.locator('.message-body').first().evaluate(e=>getComputedStyle(e).userSelect),'text');const sent=wire.find(r=>r.name==='alice');assert.ok(sent.path.includes('/m.room.encrypted/'));assert.ok(sent.data.ciphertext);assert.ok(!JSON.stringify(sent.data).includes('私聊端到端加密'));await a.page.screenshot({path:artifacts+'/web-app-private.png'})});
 await check('refresh preserves JS crypto and cached conversation',async()=>{await a.page.waitForTimeout(2200);await a.page.reload();await a.page.locator('.sidebar').waitFor({timeout:45000});await open(a.page,'bob');await a.page.locator('.message-body').filter({hasText:'网页 App 基础验证：私聊端到端加密。'}).waitFor();assert.equal(await a.page.getByText('无法解密此消息。需要此会话的历史密钥。',{exact:true}).count(),0)});
 await check('group continues to send plaintext through shared frontend',async()=>{await a.page.getByRole('button',{name:'返回会话列表',exact:true}).click();await open(a.page,'Android isolated group');await send(a.page,'网页 App 基础验证：普通群消息。');assert.ok(wire.some(r=>r.name==='alice'&&r.path.includes('/m.room.message/')&&r.data.body==='网页 App 基础验证：普通群消息。'))});
 await check('attachment download uses chunked App file bridge',async()=>{await a.page.locator('input[type=file]').setInputFiles({name:'wrapper-basic.txt',mimeType:'text/plain',buffer:Buffer.from('Isolated browser download fixture')});await a.page.getByText('已上传，待发送',{exact:true}).waitFor();await a.page.getByRole('button',{name:'发送消息',exact:true}).click();await a.page.getByRole('button',{name:'下载附件',exact:true}).click();await a.page.waitForFunction(()=>window.__calls.some(c=>c.method==='saveFinish'));const chunks=await a.page.evaluate(()=>window.__calls.filter(c=>c.method==='saveChunk').map(c=>c.args.data));assert.equal(Buffer.concat(chunks.map(c=>Buffer.from(c,'base64'))).toString(),'Isolated browser download fixture')});
 await check('offline reload keeps framework, conversation and decrypted cached messages',async()=>{await a.page.waitForTimeout(2500);await a.page.evaluate(()=>localStorage.setItem('test:offline','1'));await a.context.setOffline(true);await a.page.reload();await a.page.locator('.app-offline').waitFor({timeout:50000});await open(a.page,'bob');await a.page.locator('.message-body').filter({hasText:'网页 App 基础验证：私聊端到端加密。'}).waitFor({timeout:30000});await a.page.screenshot({path:artifacts+'/web-app-offline.png'})});
 await check('App-only backup panel validates whole days and invokes directory bridge',async()=>{await a.page.getByRole('button',{name:'返回会话列表',exact:true}).click();await a.page.getByRole('button',{name:'账户与设置',exact:true}).click();await a.page.getByRole('button',{name:'App 数据备份与导出',exact:true}).click();await a.page.getByText('内部已保存 2 份原消息包',{exact:true}).waitFor();await a.page.getByRole('spinbutton',{name:'间隔天数'}).fill('1.5');await a.page.getByLabel('独立导出口令',{exact:true}).fill('IsolatedExportPassword123');assert.ok(await a.page.getByRole('button',{name:'选择系统目录并启用定期导出',exact:true}).isDisabled());await a.page.getByRole('spinbutton',{name:'间隔天数'}).fill('2');await a.page.getByRole('button',{name:'选择系统目录并启用定期导出',exact:true}).click();await a.page.waitForFunction(()=>window.__calls.some(c=>c.method==='configureExport'&&c.args.days===2));await a.page.screenshot({path:artifacts+'/web-app-backup.png'});await a.page.getByRole('button',{name:'关闭',exact:true}).click()});
 await check('reconnection resumes synchronization',async()=>{await a.page.evaluate(()=>localStorage.removeItem('test:offline'));await a.context.setOffline(false);await a.page.waitForFunction(()=>!document.querySelector('.app-offline'),undefined,{timeout:65000})});
 assert.deepEqual(errors,[]);await writeFile(artifacts+'/result.json',JSON.stringify({checks,errors,sends:wire.map(r=>({name:r.name,type:r.path.includes('encrypted')?'encrypted':'plain'}))},null,2));console.log('PASS',checks.length);
} catch(e){await writeFile(artifacts+'/failure.txt',String(e));for(let i=0;i<contexts.length;i++){for(const page of contexts[i].pages()){await page.screenshot({path:artifacts+'/failure-'+i+'.png'}).catch(()=>{});await writeFile(artifacts+'/failure-'+i+'.txt',(await page.locator('body').innerText()).slice(0,12000)).catch(()=>{})}}console.error(String(e));process.exitCode=1}
finally{await browser.close()}
