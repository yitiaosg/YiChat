import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const context=await browser.newContext({baseURL:'http://127.0.0.1:5091'});
const page=await context.newPage(),errors=[],checks=[];
page.on('pageerror',err=>errors.push(err.message));
async function check(name,fn){await fn();checks.push(name);}
async function pace(){await page.waitForTimeout(1050);}
try{
  await mkdir('artifacts',{recursive:true});
  await page.goto('/test-login/testuser');
  await check('account menus',async()=>{for(const name of ['个人资料设置','账户与安全','隐私设置','我的二维码'])await page.getByRole('link',{name}).waitFor();});
  await check('no self follow/message',async()=>{assert.equal(await page.locator('[data-social-follow]').count(),0);assert.equal(await page.locator('[data-social-message]').count(),0);});
  await page.getByRole('link',{name:'账户与安全'}).click();
  await check('security includes login and logout',async()=>{await page.getByText('账户与安全 · 登录方式').waitFor();await page.getByRole('button',{name:'退出登录'}).waitFor();});
  await check('security contains only its options',async()=>{assert.equal(await page.locator('.acct-head').count(),0);assert.equal(await page.getByRole('link',{name:/我的 YIDT/}).count(),0);assert.equal(await page.getByRole('link',{name:'个人资料设置',exact:true}).count(),0);});
  await page.goto('/account/profile/');
  await check('profile contains only profile options',async()=>{await page.getByRole('link',{name:'更换头像'}).waitFor();await page.getByRole('link',{name:'昵称与自我介绍'}).waitFor();assert.equal(await page.locator('.acct-head').count(),0);assert.equal(await page.getByRole('button',{name:'退出登录'}).count(),0);assert.equal(await page.getByRole('link',{name:'隐私设置',exact:true}).count(),0);});
  await page.goto('/@guestuser/');
  await check('follow updates from server',async()=>{const b=page.locator('[data-social-follow]');await b.click();await page.getByRole('button',{name:'已关注',exact:true}).waitFor();assert.equal(await page.locator('#social-status').textContent(),'已保存');});
  await check('message opens real chat deep link',async()=>{await page.route('https://chat.yitiao.org/**',route=>route.fulfill({contentType:'text/html',body:'<p>Chat navigation target</p>'}));await page.locator('[data-social-message]').click();await page.waitForURL('https://chat.yitiao.org/?to=guestuser');await page.goBack();});
  await check('private lists not linked',async()=>{assert.equal(await page.locator('a[href*="/account/relations/"]').count(),0);});
  await pace();await page.goto('/account/privacy/');
  await page.locator('select[name=messages]').selectOption('friends');await page.getByLabel('公开关注名单').check();await page.getByRole('button',{name:'保存',exact:true}).click();
  await check('privacy saved',async()=>{await page.getByText('设置已保存').waitFor();assert.equal(await page.locator('select[name=messages]').inputValue(),'friends');});
  await page.locator('#block-form input').fill('guestuser');await Promise.all([page.waitForEvent('load'),page.locator('#block-form button').click()]);
  await check('blacklist username and lazy list',async()=>{await page.locator('[data-social-list] > button').scrollIntoViewIfNeeded();await page.getByRole('button',{name:'解除拉黑'}).waitFor();});
  await page.getByRole('button',{name:'解除拉黑'}).click();await page.getByText('已解除拉黑').waitFor();
  await page.goto('/@guestuser/');
  await check('unblock does not restore follow',async()=>{await page.getByRole('button',{name:'关注',exact:true}).waitFor();});
  await pace();await page.goto('/account/qr/');
  await check('QR canonical current profile',async()=>{assert.equal(await page.locator('a[href="https://yitiao.org/@testuser/"]').count(),1);await page.getByAltText('我的主页二维码').waitFor();assert.ok(await page.getByAltText('我的主页二维码').evaluate(img=>img.complete&&img.naturalWidth>0));});
  await check('QR transparent white and centered',async()=>{const values=await page.getByAltText('我的主页二维码').evaluate(img=>{const r=img.getBoundingClientRect(),canvas=document.createElement('canvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0);const p=ctx.getImageData(0,0,canvas.width,canvas.height).data;let white=false,transparent=false,invalid=false;for(let i=0;i<p.length;i+=4){if(p[i+3]===0)transparent=true;else if(p[i]===255&&p[i+1]===255&&p[i+2]===255)white=true;else invalid=true;}return {white,transparent,invalid,center:r.x+r.width/2,viewport:innerWidth};});assert.ok(values.white&&values.transparent&&!values.invalid);assert.ok(Math.abs(values.center-values.viewport/2)<1);});
  await page.screenshot({path:'artifacts/qr-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.reload();
  await check('mobile QR centered and no overflow',async()=>{const r=await page.getByAltText('我的主页二维码').boundingBox();assert.ok(Math.abs(r.x+r.width/2-195)<1);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));});
  await page.screenshot({path:'artifacts/qr-mobile.png',fullPage:true});
  await pace();await page.goto('/account/privacy/');
  await check('privacy controls styled and keyboard accessible',async()=>{const toggle=page.getByLabel('公开关注名单');await toggle.focus();await page.keyboard.press('Space');const width=await page.locator('.settings-switch-track').first().evaluate(x=>x.getBoundingClientRect().width);assert.ok(width>=44);await page.locator('select[name=messages]').selectOption('everyone');assert.equal(await page.locator('select[name=messages]').inputValue(),'everyone');});
  await page.screenshot({path:'artifacts/privacy-mobile.png',fullPage:true});
  await mkdir('artifacts',{recursive:true});await page.goto('/account/');await page.screenshot({path:'artifacts/account-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.reload();
  await check('mobile no horizontal overflow',async()=>{assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));});
  await page.screenshot({path:'artifacts/account-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);await writeFile('artifacts/account-results.json',JSON.stringify({checks,errors},null,2));
  process.stdout.write(JSON.stringify({passed:checks.length,errors})+'\n');
}finally{await browser.close();}
