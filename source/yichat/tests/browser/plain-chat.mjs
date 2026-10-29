import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
const origin=process.env.CHAT_TEST_ORIGIN,tokens=JSON.parse(process.env.CHAT_TEST_TOKENS);
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const contexts=[],wire=[],errors=[],requests=[],checks=[];
async function setup(user){
 const context=await browser.newContext({acceptDownloads:true});contexts.push(context);
 await context.addCookies([{name:'yc_session',value:tokens[user],url:origin}]);const page=await context.newPage();page.setDefaultTimeout(25000);
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{requests.push(new URL(r.url()).pathname);if(r.url().includes('/send/'))wire.push({path:r.url(),data:r.postDataJSON()})});
 await page.goto(origin);await page.locator('.sidebar').waitFor();return page;
}
async function send(page,text){await page.getByRole('textbox',{name:'消息',exact:true}).fill(text);await page.getByRole('button',{name:'发送消息',exact:true}).click();await page.locator('.outgoing').waitFor({state:'detached'})}
async function check(name,fn){console.log('CHECK',name);await fn();checks.push(name)}
try{
 await mkdir('artifacts',{recursive:true});const a=await setup('alice'),b=await setup('bob');
 let room;
 await check('private room has no encryption state; both users read normal messages',async()=>{
  const response=await a.request.post(origin+'/api/rooms',{headers:{Origin:origin},data:{kind:'direct',users:['bob'],key:crypto.randomUUID()}});assert.equal(response.status(),200);room=(await response.json()).room_id;
  await a.reload();await a.locator('.room-row').first().click();await send(a,'清理后的普通私聊消息');
  await b.locator('.room-row').first().click();await b.locator('.timeline .message-body').filter({hasText:'清理后的普通私聊消息'}).waitFor();
  const state=await a.request.get(origin+'/_matrix/client/v3/rooms/'+encodeURIComponent(room)+'/state/m.room.encryption');assert.equal(state.status(),404);
  await send(b,'收到，无需会话密钥');await a.locator('.timeline').getByText('收到，无需会话密钥',{exact:true}).waitFor();
 });
 await check('refresh and a second device read history without key setup',async()=>{
  await a.reload();await a.locator('.room-row').first().click();await a.locator('.timeline').getByText('清理后的普通私聊消息',{exact:true}).waitFor();
  const second=await setup('alice2');await second.locator('.room-row').first().click();await second.locator('.timeline').getByText('清理后的普通私聊消息',{exact:true}).waitFor();await second.context().close();
 });
 await check('attachments upload without E2EE and authenticated download matches bytes',async()=>{
  await a.locator('input[type=file]').setInputFiles({name:'plain-note.txt',mimeType:'text/plain',buffer:Buffer.from('authenticated plain attachment')});await a.getByText('已上传，待发送',{exact:true}).waitFor();await a.getByRole('button',{name:'发送消息',exact:true}).click();await b.getByText('plain-note.txt',{exact:true}).waitFor();
  const download=b.waitForEvent('download');await b.getByRole('button',{name:'下载附件',exact:true}).click();assert.equal((await readFile(await (await download).path())).toString(),'authenticated plain attachment');
  const event=wire.find(r=>r.data.body==='plain-note.txt');assert.ok(event?.data.url);assert.equal(event.data.file,undefined);
 });
 await check('large upload survives lost chunk and final acknowledgments',async()=>{
  const encoded=await a.evaluate(()=>{
   const canvas=document.createElement('canvas');canvas.width=320;canvas.height=320;const context=canvas.getContext('2d'),image=context.createImageData(320,320);let seed=1;
   for(let i=0;i<image.data.length;i+=4){seed=(Math.imul(seed,1664525)+1013904223)|0;image.data[i]=seed&255;image.data[i+1]=(seed>>>8)&255;image.data[i+2]=(seed>>>16)&255;image.data[i+3]=255}
   context.putImageData(image,0,0);return canvas.toDataURL('image/png').split(',')[1];
  });
  const bytes=Buffer.from(encoded,'base64');assert.ok(bytes.length>200000);let chunkLost=false,finishLost=false,putCount=0;
  await a.route('**/api/uploads/*',async route=>{
   if(route.request().method()==='PUT'){putCount++;if(!chunkLost){chunkLost=true;await route.fetch();await route.abort('failed');return}}
   await route.continue();
  });
  await a.route('**/api/uploads/*/complete',async route=>{if(!finishLost){finishLost=true;await route.fetch();await route.abort('failed')}else await route.continue()});
  await a.locator('input[type=file]').setInputFiles({name:'phone-upload.png',mimeType:'image/png',buffer:bytes});
  await a.getByText('已上传，待发送',{exact:true}).waitFor();
  assert.ok(chunkLost&&finishLost);assert.equal(putCount,Math.ceil(bytes.length/131072),'no confirmed bytes retransmitted');
  await a.unroute('**/api/uploads/*');await a.unroute('**/api/uploads/*/complete');
  await a.getByRole('button',{name:'发送消息',exact:true}).click();await b.getByText('phone-upload.png',{exact:true}).waitFor();
  const download=b.waitForEvent('download');await b.locator('.media-content').filter({hasText:'phone-upload.png'}).getByRole('button',{name:'下载附件',exact:true}).click();
  assert.deepEqual(await readFile(await (await download).path()),bytes);
  await b.locator('.media-content').filter({hasText:'phone-upload.png'}).locator('img').waitFor();
  assert.ok(await b.locator('.media-content').filter({hasText:'phone-upload.png'}).locator('img').evaluate(image=>image.naturalWidth===320));
 });
 await check('station URLs still render game and real card previews on both sides',async()=>{
  await send(a,'https://yitiao.org/games/parachute/share/'+'G'.repeat(32)+'/');await a.locator('.game-art').waitFor();await b.locator('.game-art').waitFor();
  await send(a,'https://yitiao.org/ycards/card/'+'a'.repeat(32)+'/WEB-CID/');await a.locator('.card-art canvas.yc-canvas').waitFor();await b.locator('.card-art canvas.yc-canvas').waitFor();
 });
 await check('preview-only shares hide raw URLs on both sides and retain captions',async()=>{
  for(const page of [a,b]){
   assert.equal(await page.locator('.timeline .message-body').filter({hasText:'https://yitiao.org/'}).count(),0);
   assert.equal(await page.locator('.sidebar .room-summary').filter({hasText:'https://yitiao.org/'}).count(),0);
   assert.equal(await page.locator('.timeline .card-art canvas.yc-canvas').count(),1);
  }
  const url='https://yitiao.org/ycards/card/'+'a'.repeat(32)+'/WEB-CID/';
  await send(a,'看看这张卡片 '+url);
  await b.locator('.timeline .message-body').getByText('看看这张卡片',{exact:true}).waitFor();
  assert.equal(await b.locator('.timeline .message-body').filter({hasText:url}).count(),0);
  assert.ok(wire.some(row=>row.data.body==='看看这张卡片 '+url),'stored message still contains only URL and caption, not card data');
 });
 await check('client never initializes E2EE or sends ciphertext',async()=>{
  assert.ok(wire.length>=5);assert.ok(wire.every(r=>r.path.includes('/m.room.message/')&&!r.data.ciphertext));
  assert.equal(requests.filter(p=>/\/(keys\/(query|upload|claim)|room_keys\/)/.test(p)).length,0);
  assert.deepEqual(errors,[]);await a.screenshot({path:'artifacts/plain-chat.png',fullPage:true});
 });
 await writeFile('artifacts/plain-results.json',JSON.stringify({checks,errors,plainMessages:wire.length},null,2));console.log(JSON.stringify({passed:checks.length}));
}catch(e){for(const c of contexts)for(const p of c.pages())await p.screenshot({path:'artifacts/plain-failure.png',fullPage:true}).catch(()=>{});throw e}
finally{await browser.close()}
