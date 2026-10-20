import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const context=await browser.newContext({acceptDownloads:true});
await context.addCookies([{name:'yc_session',value:process.env.ARCHIVE_TEST_TOKEN,url:process.env.ARCHIVE_TEST_ORIGIN,httpOnly:true}]);
const page=await context.newPage(),errors=[],uploads=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('request',request=>{if(request.method()!=='GET')uploads.push(request.method());});
try{
  await page.goto(process.env.ARCHIVE_TEST_ORIGIN+'/archives/');
  await page.locator('#room').fill('!group');await page.locator('#day').fill('2026-01-02');
  await page.getByRole('button',{name:'获取可信摘要'}).click();
  await page.getByText('已从认证服务器取得可信摘要',{exact:true}).waitFor();
  await page.locator('#local-file').setInputFiles(process.env.ARCHIVE_TEST_FILE);
  await page.getByText('校验通过：与服务器存证逐字节一致',{exact:true}).waitFor();
  const bytes=await readFile(process.env.ARCHIVE_TEST_FILE);bytes[bytes.length-1]^=1;
  const bad=process.env.ARCHIVE_TEST_FILE+'.tampered';await writeFile(bad,bytes);
  await page.locator('#local-file').setInputFiles(bad);
  await page.getByText('校验失败：文件、日期或规格不匹配',{exact:true}).waitFor();
  const next=page.waitForEvent('download');await page.locator('#download').click();
  const download=await next;assert.equal(await download.failure(),null);
  assert.deepEqual(await readFile(await download.path()),await readFile(process.env.ARCHIVE_TEST_FILE));
  assert.deepEqual(uploads,[]);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:4,uploadedFiles:0,errors}));
}finally{await browser.close();}
