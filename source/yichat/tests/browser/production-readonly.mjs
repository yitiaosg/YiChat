// Anonymous read-only smoke check. Does not sign in or create business operations.
import {chromium} from 'playwright';import assert from 'node:assert/strict';import {resolve} from 'node:path';import {writeFile} from 'node:fs/promises';
const output=process.env.YICHAT_DELIVERY?resolve(process.env.YICHAT_DELIVERY):resolve(import.meta.dirname,'../../../../delivery/production-20261021'),browser=await chromium.launch({headless:true,args:['--no-sandbox']}),errors=[];
try{
 for(const [name,viewport] of [['desktop',{width:1440,height:1000}],['mobile',{width:412,height:915}]]){
  const context=await browser.newContext({viewport}),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.goto('https://chat.yitiao.org/',{waitUntil:'networkidle'});await page.getByRole('link',{name:'使用 Yitiao 账户登录'}).waitFor();assert.ok(await page.locator('.welcome-mark').evaluate(e=>e.naturalWidth===1024));assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:output+'/screenshots/production-'+name+'.png',fullPage:true});
  const health=await context.request.get('https://chat.yitiao.org/health');assert.equal(health.status(),200);await page.goto('https://yitiao.org/');assert.ok(await page.locator('a[href="https://chat.yitiao.org/"]').count());await page.goto('https://yitiao.org/account/');assert.ok(/login|account/.test(page.url()));await context.close();
 }
 assert.deepEqual(errors,[]);await writeFile(output+'/production-result.json',JSON.stringify({desktop:'1440x1000',mobile:'412x915',health:200,brand:true,mainChatLink:true,errors},null,2));console.log('PASS anonymous production desktop/mobile');
}finally{await browser.close()}
