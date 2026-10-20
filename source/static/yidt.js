'use strict';const $=id=>document.getElementById(id);
if($('oauth-login'))setTimeout(()=>location.assign('/login'),500);
if($('toggle-assets')){let hidden=localStorage.getItem('yidt.hidden')==='1',html=$('assets').innerHTML;const render=()=>{$('assets').innerHTML=hidden?'***':html;$('asset-change').hidden=hidden;$('toggle-assets').setAttribute('aria-pressed',String(hidden));};render();$('toggle-assets').onclick=()=>{hidden=!hidden;localStorage.setItem('yidt.hidden',hidden?'1':'0');render();};}
if($('add-wallet'))$('add-wallet').onclick=()=>{$('wallet-create').hidden=false;$('wallet-create').scrollIntoView({behavior:'smooth'});};
document.querySelectorAll('form[data-confirm]').forEach(f=>f.addEventListener('submit',e=>{if(!confirm(f.dataset.confirm))e.preventDefault();}));
if($('secret')){const hide=()=>{$('secret').textContent='内容已隐藏';$('hide-secret').hidden=true;$('backup-answer').hidden=false;};$('hide-secret').onclick=hide;setTimeout(hide,60000);document.addEventListener('visibilitychange',()=>{if(document.hidden)hide();});window.addEventListener('pagehide',hide);}
if($('send-amount'))$('send-amount').addEventListener('input',()=>{try{const raw=$('send-amount').value;if(!/^\d+(\.\d+)?$/.test(raw))throw 0;const [a,b='']=raw.split('.'),n=BigInt(a)*10n**16n+BigInt((b+'0'.repeat(16)).slice(0,16)),bal=BigInt($('send-preview').dataset.balance),gas=39062500000000n,d=bal>=n+gas?n+gas:bal,credit=d-gas;const fmt=v=>{const s=v.toString().padStart(17,'0');return(s.slice(0,-16)+'.'+s.slice(-16)).replace(/\.?0+$/,'');};$('send-preview').textContent=bal<n||credit<=0n?'余额或金额不足':'实际扣款 '+fmt(d)+' YIDT；对方到账 '+fmt(credit)+' YIDT';}catch{$('send-preview').textContent='';}});
if($('scan'))$('scan').onclick=async()=>{let stream;try{
 const detector='BarcodeDetector'in window?new BarcodeDetector({formats:['qr_code']}):null;
 stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});const video=$('camera');video.srcObject=stream;video.hidden=false;await video.play();let stopped=false;
 const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d',{willReadFrequently:true});
 const stop=()=>{stopped=true;stream.getTracks().forEach(t=>t.stop());video.hidden=true;};setTimeout(stop,30000);
 const tick=async()=>{if(stopped)return;try{let value;if(detector){const codes=await detector.detect(video);value=codes[0]?.rawValue;}else if(window.jsQR&&video.videoWidth){canvas.width=video.videoWidth;canvas.height=video.videoHeight;ctx.drawImage(video,0,0);const im=ctx.getImageData(0,0,canvas.width,canvas.height);value=jsQR(im.data,im.width,im.height)?.data;}
 if(value&&/^yidt-[a-z0-9]{12,16}$/.test(value)){$('destination').value=value;stop();return;}}catch{}setTimeout(tick,250);};tick();document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();},{once:true});
 }catch(e){if(stream)stream.getTracks().forEach(t=>t.stop());alert('无法打开相机，请检查相机权限或手动填写地址。');}};

if(!document.body.classList.contains('public')&&!document.getElementById('oauth-login')){
 let checking=false;const checkSession=async()=>{if(checking||document.hidden)return;checking=true;try{const r=await fetch('/session-state',{cache:'no-store'});if(!r.headers.get('content-type')?.includes('application/json'))location.reload();}catch{}finally{checking=false;}};
 setInterval(checkSession,10000);window.addEventListener('focus',checkSession);
}
