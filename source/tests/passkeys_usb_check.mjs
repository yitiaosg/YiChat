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
await send('WebAuthn.enable',{enableUI:false});const {authenticatorId}=await send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'usb',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
try{
await nav('https://yitiao.org/test-login/testuser');await wait(`document.body.innerText.includes('今日可领')`);await nav('https://yidt.yitiao.org/passkeys/');await wait(`!!document.querySelector('[name=management_pin]')`);await field('[name=management_pin]','123456');await field('[name=passkey_name]','USB 安全密钥');await click('[data-passkey-add]');await wait(`!!document.querySelector('.passkey-item')`);
const info=await(await fetch('http://127.0.0.1:5081/test-info')).json(),mine=info.wallets.find(w=>w.user==='testuser').address,other=info.wallets.find(w=>w.user==='guestuser').address;
await nav('https://yidt.yitiao.org/wallet/'+mine+'/send');await wait(`!!document.querySelector('[name=destination]')`);await field('[name=destination]',other);await field('[name=amount]','1');await click('form button:not([type])');await wait(`location.pathname.startsWith('/explorer/tx/')`);
console.log(JSON.stringify({ok:!errors.length,errors,checks:['CTAP2 USB authenticator registration','USB user-verified wallet transfer']}));if(errors.length)process.exitCode=1;
}catch(e){console.error(e);process.exitCode=1;}finally{await send('WebAuthn.removeVirtualAuthenticator',{authenticatorId});ws.close();}
