import fs from 'node:fs';
const artifacts=process.env.YCARDS_ARTIFACTS||new URL('./ycards-collection-artifacts',import.meta.url).pathname;fs.mkdirSync(artifacts,{recursive:true});
const pages=await(await fetch('http://127.0.0.1:9225/json')).json(),ws=new WebSocket(pages[0].webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));let acceptDialogs=true,confirmations=0;let seq=0;const pending=new Map(),errors=[],writes=[],requests=[],failed=[];
const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
ws.addEventListener('message',async e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p?.reject(Error(JSON.stringify(m.error))):p?.resolve(m.result)}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);else if(m.method==='Network.requestWillBeSent'){requests.push({id:m.params.requestId,url:m.params.request.url});if(m.params.request.method==='POST')writes.push(m.params.request.url)}else if(m.method==='Network.loadingFailed')failed.push(m.params.requestId);else if(m.method==='Page.javascriptDialogOpening'){confirmations++;await send('Page.handleJavaScriptDialog',{accept:acceptDialogs,promptText:'V2Demo'})}});
await send('Page.enable');await send('Runtime.enable');await send('Network.enable');await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
const evaljs=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value};
const wait=async expression=>{for(let i=0;i<150;i++){if(await evaljs(expression))return;await new Promise(r=>setTimeout(r,100))}throw Error('Timeout '+expression)};
const click=async sel=>{await wait(`!!document.querySelector(${JSON.stringify(sel)})`);await evaljs(`document.querySelector(${JSON.stringify(sel)}).click()`)};
const field=async(sel,value)=>{await wait(`!!document.querySelector(${JSON.stringify(sel)})`);return evaljs(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`)};
const nav=async path=>{await send('Page.navigate',{url:'http://127.0.0.1:5079'+path});await wait(`document.readyState==='complete'`)};
const screen=async name=>{const s=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(artifacts+'/'+name+'.png',Buffer.from(s.data,'base64'))};

const fixture=()=>evaljs(`fetch('/test-state').then(r=>r.json())`);
await nav('/test-login/creator');await wait(`document.querySelectorAll('[data-select-card]').length===24`);
const protectedIds=await evaljs(`fetch('/ycards/api/mine?compact=1').then(r=>r.json()).then(r=>r.items.filter(c=>c.listing_id||c.auction_id||c.showcased).map(c=>c.id))`),protectedCount=protectedIds.length;
if(protectedCount!==3)throw Error('Expected three protected cards');
await click(`[data-select-card="${protectedIds[0]}"]`);if(!await evaljs(`document.querySelector('#destroy-selected').disabled`))throw Error('Protected selection allows destruction');await click('#clear-cards');
await evaljs(`Array.from(document.querySelectorAll('[data-select-card]')).filter(e=>!${JSON.stringify(protectedIds)}.includes(e.dataset.selectCard)).slice(0,2).forEach(e=>e.click())`);
acceptDialogs=false;await click('#destroy-selected');await new Promise(r=>setTimeout(r,200));if((await fixture()).destroyed!==0)throw Error('Cancelled destruction changed data');
acceptDialogs=true;await click('#destroy-selected');await wait(`document.querySelector('#toast').textContent.includes('已损毁 2 张')`);if((await fixture()).destroyed!==2)throw Error('Batch destruction failed');
await wait(`document.querySelectorAll('[data-select-card]').length===24`);
const single=await evaljs(`Array.from(document.querySelectorAll('[data-select-card]')).find(e=>!${JSON.stringify(protectedIds)}.includes(e.dataset.selectCard)).dataset.selectCard`);
await evaljs(`document.querySelector('.preview[data-id="${single}"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))`);await click('#destroy-card');await wait(`!document.querySelector('#modal').open`);if((await fixture()).destroyed!==3)throw Error('Single destruction failed');
await nav('/account/changeprofile/');await wait(`document.querySelectorAll('#yc-showcase-grid .yc-canvas').length===1`);
const before=(await fixture()).balance;
for(let n=4;n<=6;n++){await click('#yc-buy-slot');await wait(`document.querySelectorAll('#yc-showcase-grid .yc-display-slot').length===${n}`)}
if((await fixture()).balance!==before-3000000)throw Error('Wrong price for three frames');if(!await evaljs(`document.querySelector('#yc-buy-slot').hidden`))throw Error('Purchase cap missing');
for(let slot=2;slot<=6;slot++){
 await click(`[data-choose="${slot}"]`);await wait(`document.querySelectorAll('#yc-picker-cards [data-pick]:not(:disabled)').length>0`);await click('#yc-picker-cards [data-pick]:not(:disabled)');await wait(`!document.querySelector('#yc-card-picker').open && document.querySelectorAll('#yc-showcase-grid .yc-canvas').length===${slot}`);
}
await screen('showcase-editor-desktop');
await nav('/test-logout');await wait(`document.querySelectorAll('#yc-showcase-grid .yc-canvas').length===6`);
const desktop=await evaljs(`({columns:getComputedStyle(document.querySelector('#yc-showcase-grid')).gridTemplateColumns.split(' ').length,width:innerWidth,scroll:document.documentElement.scrollWidth,belowBio:document.querySelector('#yc-showcase').getBoundingClientRect().top>=document.querySelector('.bio').getBoundingClientRect().bottom})`);
if(desktop.columns!==6||!desktop.belowBio)throw Error('Desktop profile layout wrong');
const clean=await evaljs(`({text:document.querySelector('#yc-showcase').innerText.trim(),slots:[...document.querySelectorAll('.yc-display-slot')].every(e=>getComputedStyle(e).borderTopWidth==='0px'&&getComputedStyle(e).backgroundImage==='none'),heading:!!document.querySelector('.yc-showcase-heading')})`);
if(clean.text||!clean.slots||clean.heading)throw Error('Public showcase contains chrome '+JSON.stringify(clean));
await screen('showcase-public-desktop');
const href=await evaljs('location.href');await click('#yc-showcase-grid .yc-canvas');if(await evaljs('location.href')!==href)throw Error('Public card navigated');
if(!await evaljs(`Array.from(document.querySelectorAll('#yc-showcase-grid .card-stage')).every(e=>e._ycRenderer.onSelect===null)`))throw Error('Public card click handler exists');
if(await evaljs(`!!document.querySelector('#yc-buy-slot')||!!document.querySelector('[data-choose]')`))throw Error('Public management controls leaked');
await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await evaljs(`document.querySelector('#yc-showcase').scrollIntoView({block:'center'})`);await screen('showcase-public-mobile');
const mobile=await evaljs(`({columns:getComputedStyle(document.querySelector('#yc-showcase-grid')).gridTemplateColumns.split(' ').length,width:innerWidth,scroll:document.documentElement.scrollWidth})`);if(mobile.columns!==3||mobile.width!==mobile.scroll)throw Error('Mobile profile layout wrong');
await nav('/test-login/creator');await nav('/account/changeprofile/');await wait(`document.querySelectorAll('[data-remove]').length===6`);await click('[data-remove="1"]');await wait(`document.querySelectorAll('[data-remove]').length===5`);
await screen('showcase-editor-mobile');
await field('#nick','更新的昵称');await field('#bio','保存介绍仍然可用');await click('#saveBtn');await wait(`location.pathname==='/account/'`);
await nav('/@creator');await wait(`document.querySelector('h1').textContent==='更新的昵称' && document.querySelectorAll('#yc-showcase-grid .yc-canvas').length===5`);
const result={publicCardsOnly:true,errors,confirmations,protectedCount,destroyed:(await fixture()).destroyed,purchasedSlots:(await fixture()).showcase.slots,desktop,mobile,publicClickNoAction:true,profileSave:true};console.log(JSON.stringify(result,null,2));fs.writeFileSync(artifacts+'/result.json',JSON.stringify(result,null,2));ws.close();if(errors.length)process.exitCode=1;
