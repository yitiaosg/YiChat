import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
const page=await browser.newPage({viewport:{width:500,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{
 Object.defineProperty(navigator,'onLine',{get:()=>false});
 window.YiChatApp={postMessage(raw){const r=JSON.parse(raw);if(r.method==='messagePreview')window.offlineReads.push(r.args);queueMicrotask(()=>window.YiChatApp.onmessage?.({data:JSON.stringify({id:r.id,ok:true,value:r.method==='messagePreview'?(window.offlineStore[r.args.room+'|'+r.args.resource]??null):{}})}))}};
});
await page.goto((process.env.YICHAT_BROWSER_BASE||'http://127.0.0.1:5188')+'/test/browser/offline-previews.html');
await page.getByRole('button',{name:'查看收款回执'}).waitFor();assert.equal(await page.locator('.money-bubble').count(),2);
assert.match(await page.getByRole('button',{name:'查看收款回执'}).innerText(),/已收款/);
await page.locator('.gift-message').scrollIntoViewIfNeeded();await page.getByRole('heading',{name:'离线赠卡',exact:true}).waitFor();await page.locator('.gift-art canvas.yc-canvas').waitFor();
await page.locator('.record-card button').click();await page.getByRole('dialog').getByText('离线聊天记录',{exact:true}).waitFor();
assert.deepEqual(await page.evaluate(()=>window.apiLoads),[]);assert.ok(await page.evaluate(()=>window.offlineReads.some(r=>r.resource.startsWith('gift:'))&&window.offlineReads.some(r=>r.resource.startsWith('record:'))));
const output=process.env.YICHAT_BROWSER_OUTPUT||'/tmp/yichat-0314-browser';await mkdir(output,{recursive:true});await page.screenshot({path:output+'/offline-record.png',fullPage:true});await page.getByRole('button',{name:'关闭',exact:true}).click();await page.screenshot({path:output+'/offline-previews.png',fullPage:true});const store=await page.evaluate(()=>window.offlineStore),hash=Object.keys(store).find(k=>k.startsWith('|record:')).slice('|record:'.length);
const full=await browser.newPage(),apiCalls=[];full.on('pageerror',e=>errors.push(e.message));await full.addInitScript(saved=>{Object.defineProperty(navigator,'onLine',{get:()=>false});window.YiChatApp={postMessage(raw){const r=JSON.parse(raw);queueMicrotask(()=>window.YiChatApp.onmessage?.({data:JSON.stringify({id:r.id,ok:true,value:saved[r.args.room+'|'+r.args.resource]??null})}))}}},store);await full.route('**/api/**',route=>{apiCalls.push(route.request().url());return route.abort()});await full.goto((process.env.YICHAT_BROWSER_BASE||'http://127.0.0.1:5188')+'/records/'+hash);await full.locator('.record-page').getByText('离线聊天记录',{exact:true}).waitFor();await full.waitForFunction(()=>!document.querySelector('[data-avatar-loading="true"]'));assert.deepEqual(apiCalls,[]);assert.doesNotMatch(await full.locator('.record-expiry').innerText(),/Invalid Date/);await full.screenshot({path:output+'/offline-full-page.png',fullPage:true});
assert.deepEqual(errors,[]);console.log('PASS: unopened native backup snapshots render transfers, verified receipts, gift card art, URL-only merged records and actual full-screen entry offline, with zero API calls');await browser.close();
