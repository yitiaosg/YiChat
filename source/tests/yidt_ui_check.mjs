import fs from 'node:fs';
const dir=new URL('./yidt-artifacts',import.meta.url).pathname;fs.mkdirSync(dir,{recursive:true});
const pages=await(await fetch('http://127.0.0.1:9235/json')).json(),ws=new WebSocket(pages[0].webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));let seq=0;const pending=new Map(),errors=[],responses=[];
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
ws.addEventListener('message',async e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p?.reject(Error(JSON.stringify(m.error))):p?.resolve(m.result);return;}
 if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);
 if(m.method==='Page.javascriptDialogOpening'){errors.push('Dialog: '+m.params.message);await send('Page.handleJavaScriptDialog',{accept:true});}
 if(m.method==='Fetch.requestPaused'){
  const {requestId,request}=m.params,u=new URL(request.url);try{
   if(!['yitiao.org','yidt.yitiao.org'].includes(u.hostname)){await send('Fetch.failRequest',{requestId,errorReason:'BlockedByClient'});return;}
   const port=u.hostname==='yidt.yitiao.org'?5083:u.pathname.startsWith('/games/parachute/')?5082:5081;
   const headers={...request.headers,host:u.host,'X-Forwarded-Proto':'https','X-Forwarded-Host':u.host};delete headers['content-length'];
   const r=await fetch('http://127.0.0.1:'+port+u.pathname+u.search,{method:request.method,headers,body:request.postData,redirect:'manual'});
   const hs=[...r.headers.entries()].filter(([k])=>!['set-cookie','content-encoding','content-length'].includes(k)).map(([name,value])=>({name,value}));for(const value of r.headers.getSetCookie())hs.push({name:'set-cookie',value});
   const body=Buffer.from(await r.arrayBuffer()).toString('base64');responses.push([u.pathname,r.status]);await send('Fetch.fulfillRequest',{requestId,responseCode:r.status,responseHeaders:hs,body});
  }catch(e){if(!e.message.includes('Invalid InterceptionId'))errors.push('Proxy: '+e.message);try{await send('Fetch.failRequest',{requestId,errorReason:'Failed'});}catch{}}
 }
});
await send('Page.enable');await send('Runtime.enable');await send('Network.enable');await send('Fetch.enable',{patterns:[{urlPattern:'*'}]});await send('Network.clearBrowserCookies');await send('Emulation.setDeviceMetricsOverride',{width:1360,height:960,deviceScaleFactor:1,mobile:false});
const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value;};
const wait=async expr=>{for(let i=0;i<150;i++){try{if(await evaluate(expr))return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('Timeout '+expr+'\n'+JSON.stringify({url:await evaluate('location.href'),body:await evaluate('document.body.innerText'),errors,responses:responses.slice(-15)}));};
const nav=async url=>{await send('Page.navigate',{url});await new Promise(r=>setTimeout(r,150));};
const click=async selector=>{await wait(`document.readyState==='complete'&&!!document.querySelector(${JSON.stringify(selector)})`);await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);};
const field=async(selector,value)=>evaluate(`document.querySelector(${JSON.stringify(selector)}).value=${JSON.stringify(value)}`);
const screen=async name=>{const r=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(dir+'/'+name+'.png',Buffer.from(r.data,'base64'));};
try{
await nav('https://yitiao.org/test-login/testuser');await wait(`document.body.innerText.includes('今日可领')`);
if(await evaluate(`document.body.innerText.includes('gas')||!!document.querySelector('a[href="/account/yidt/pin/"]')`))throw Error('Account UI regression');await new Promise(r=>setTimeout(r,400));await screen('daily-simple-before');
await nav('https://yidt.yitiao.org/');await wait(`!!document.querySelector('#assets')`);await click('a[href="/pin/"]');await wait(`document.body.innerText.includes('支付密码管理')`);await click('a[href^="https://yitiao.org/account/yidt/verify/"]');await wait(`!!document.querySelector('[name=password]')`);await field('[name=password]','TestPassword123!');await click('form button');await wait(`location.hostname==='yidt.yitiao.org'&&!!document.querySelector('[name=new_pin]')`);await field('[name=new_pin]','123456');await field('[name=confirm_pin]','123456');await click('form button');await wait(`location.pathname==='/'&&!!document.querySelector('#assets')`);
await nav('https://yidt.yitiao.org/services/daily/');await click('form button');await wait(`!!document.querySelector('#yidt-claim')`);await click('#yidt-claim');await wait(`document.querySelector('#yidt-claim')?.disabled&&document.body.innerText.includes('领取详情')`);await new Promise(r=>setTimeout(r,400));await screen('daily-simple-after');
await nav('https://yidt.yitiao.org/explorer/');await wait(`document.body.innerText.includes('我的钱包 · 最近交易')`);if(!await evaluate(`document.querySelectorAll('.tx').length>0`))throw Error('Own explorer transactions missing');
await nav('https://yitiao.org/games/parachute/');await wait(`!!document.querySelector('#startButton')&&!document.querySelector('#setupPanel').hidden`);
await evaluate(`showPanel('jumped');updateText({status:'jumped',paid_tx:'a'.repeat(64),redeem_tx:'b'.repeat(64),height:12,crash_height:15,stake_cents:1000,payout_cents:1200,profit_cents:200,fee_cents:50,wallet_delta_cents:150});`);
if(!await evaluate(`document.querySelector('#yidt-transactions').textContent==='下注详情赎回详情'&&document.querySelector('#yidt-transactions').getBoundingClientRect().top>=document.querySelector('.result-actions').getBoundingClientRect().bottom&&getComputedStyle(document.querySelector('#yidt-transactions a')).color==='rgb(154, 165, 180)'`))throw Error('Parachute receipts placement/style');
await evaluate(`document.querySelector('#yidt-transactions').scrollIntoView({block:'nearest'})`);await screen('parachute-details-below-buttons');
await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await nav('https://yitiao.org/account/');await wait(`document.body.innerText.includes('今日已领')`);await new Promise(r=>setTimeout(r,400));await screen('daily-simple-mobile');if(await evaluate('document.documentElement.scrollWidth>innerWidth'))throw Error('Account overflow');
console.log(JSON.stringify({ok:!errors.length,errors,checks:['compact daily activity before/after claim','PIN setup displayed and saved on YIDT','own explorer history','parachute receipt labels, grey styling and placement','account mobile layout']}));if(errors.length)process.exitCode=1;
}catch(e){console.error(e);await screen('ui-followup-failure');process.exitCode=1;}finally{ws.close();}
