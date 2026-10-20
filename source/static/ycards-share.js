/* Posters stay on the device. The QR encoder is shared with the existing site. */
window.createYCardsSharing=function(ctx){
 'use strict';
 const {api,boot,guard,dialog,modalBody,esc,toast}=ctx;
 const $=s=>modalBody.querySelector(s);
 let version=0,objectUrl=null;
 function dispose(){version++;if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=null}
 async function poster(card,url){
  const host=document.createElement('div');host.className='editor-stage';host.style='position:fixed;left:-10000px;top:0;width:780px;height:900px;pointer-events:none';document.body.append(host);
  const r=new YCardRenderer(host,card,{interactive:false});
  try{
   await r.ready;r.visible=true;r.rx=r.ry=0;r.draw();
   const c=document.createElement('canvas');c.width=1000;c.height=1400;const x=c.getContext('2d');x.fillStyle='#081b30';x.fillRect(0,0,c.width,c.height);
   x.fillStyle='#a8d9f7';x.font='600 30px sans-serif';x.fillText('YCards / YITIAO',60,65);
   x.fillStyle='#fff';x.font='700 42px sans-serif';x.fillText(card.name,60,135,880);
   const size=Math.min(840/r.canvas.width,950/r.canvas.height),w=r.canvas.width*size,h=r.canvas.height*size,left=(1000-w)/2,top=185+(950-h)/2;
   for(const plane of r.backgroundPlanes||[]){if(plane.style.mixBlendMode==='difference'){x.save();x.globalCompositeOperation='difference';x.drawImage(plane,left,top,w,h);x.restore()}}
   x.drawImage(r.canvas,left,top,w,h);
   const qr=qrcode(0,'M');qr.addData(url);qr.make();const n=qr.getModuleCount(),step=Math.floor(205/(n+8)),sizeQR=(n+8)*step,qx=60,qy=1160;
   x.fillStyle='#fff';x.fillRect(qx,qy,sizeQR,sizeQR);x.fillStyle='#000';for(let row=0;row<n;row++)for(let col=0;col<n;col++)if(qr.isDark(row,col))x.fillRect(qx+(col+4)*step,qy+(row+4)*step,step,step);
   x.fillStyle='#eef6ff';x.font='600 26px sans-serif';x.fillText('扫码查看互动卡片',qx+sizeQR+30,qy+45,660);
   x.fillStyle='#a7c2dc';x.font='22px monospace';x.fillText(card.cid,qx+sizeQR+30,qy+95,660);
   x.font='20px sans-serif';x.fillText('卡片转手后，此链接自动失效',qx+sizeQR+30,qy+137,660);
   return await new Promise((resolve,reject)=>c.toBlob(b=>b?resolve(b):reject(Error('图片生成失败')),'image/png'));
  }finally{r.destroy();host.remove()}
 }
 async function open(card){
  if(!guard(card.auction_id?'?tab=auction&auction='+card.auction_id:'?tab=market&card='+card.id))return;
  const response=await api('share',{instance_id:card.id,expected_owner:card.owner});
  card.public=true;
  const url=new URL(response.url,location.origin).href;
  dialog(`<h2>分享「${esc(card.name)}」</h2><p class="helper">卡片已公开，持有链接的人可查看；交易或竞拍转手后自动退出公开状态。</p><label>卡片链接<input id="share-url" readonly value="${esc(url)}"></label><div class="row"><button id="copy-share-url">复制链接</button><button id="native-share-url">分享链接</button><button id="native-share-image" disabled>分享图片</button><a id="download-share-image" class="button" hidden>下载图片</a></div><p id="share-status" role="status">正在生成正面分享图…</p><img id="share-poster" alt="卡片正面与分享二维码" hidden>`);
  const v=++version;
  $('#copy-share-url').onclick=async()=>{try{await navigator.clipboard.writeText(url);toast('链接已复制')}catch{const input=$('#share-url');input.select();if(document.execCommand('copy'))toast('链接已复制');else toast('请复制已选中的链接')}};
  $('#native-share-url').onclick=async()=>{if(!navigator.share){$('#copy-share-url').click();return}try{await navigator.share({title:card.name,text:card.cid,url})}catch(e){if(e.name!=='AbortError')toast(e.message)}};
  try{
   const blob=await poster(card,url);if(v!==version||!$('#share-poster'))return;
   if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=URL.createObjectURL(blob);
   const filename=`YCards-${card.cid}.png`,file=new File([blob],filename,{type:'image/png'}),img=$('#share-poster'),a=$('#download-share-image');img.src=objectUrl;img.hidden=false;a.href=objectUrl;a.download=filename;a.hidden=false;
   $('#native-share-image').disabled=false;$('#native-share-image').onclick=async()=>{try{if(navigator.canShare?.({files:[file]}))await navigator.share({files:[file],title:card.name});else a.click()}catch(e){if(e.name!=='AbortError')toast(e.message)}};
   $('#share-status').textContent='图片已生成，可分享或保存。';
  }catch(e){if(v===version&&$('#share-status'))$('#share-status').textContent=e.message}
 }
 return {open,dispose};
};
