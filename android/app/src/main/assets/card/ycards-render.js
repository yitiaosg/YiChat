/* One renderer for templates, editor, market and owned connected products.
   Stable effect names are documented in YCARDS.md. No network assets. */
(() => {
'use strict';
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const cache=new Map();
function texture(src,binary=false){
 if(!src)return null;
 const key=(binary?'mask:':'img:')+src;
 if(cache.has(key))return cache.get(key);
 const record={image:null,promise:null};
 record.promise=new Promise((resolve)=>{const im=new Image(); im.onload=()=>{
  if(binary){const c=document.createElement('canvas');c.width=Math.min(2048,im.naturalWidth||512);c.height=Math.min(2048,im.naturalHeight||512);const ctx=c.getContext('2d',{willReadFrequently:true});ctx.drawImage(im,0,0,c.width,c.height);try{const p=ctx.getImageData(0,0,c.width,c.height);for(let i=0;i<p.data.length;i+=4){p.data[i]=p.data[i+1]=p.data[i+2]=255;p.data[i+3]=p.data[i+3]?255:0}ctx.putImageData(p,0,0)}catch(e){resolve(null);return}record.image=c;
  }else record.image=im;
  resolve(record.image);
 };im.onerror=()=>resolve(null);im.src=src});
 cache.set(key,record);if(cache.size>180)cache.delete(cache.keys().next().value);return record;
}
function rounded(ctx,x,y,w,h,r){
 const [tl,tr,bl,br]=r.map(n=>clamp(n,0,Math.min(w,h)/2));
 ctx.beginPath();ctx.moveTo(x+tl,y);ctx.lineTo(x+w-tr,y);ctx.arcTo(x+w,y,x+w,y+tr,tr);ctx.lineTo(x+w,y+h-br);ctx.arcTo(x+w,y+h,x+w-br,y+h,br);ctx.lineTo(x+bl,y+h);ctx.arcTo(x,y+h,x,y+h-bl,bl);ctx.lineTo(x,y+tl);ctx.arcTo(x,y,x+tl,y,tl);ctx.closePath();
}
// A physical outline in millimetres. Legacy instances are interpreted from their
// immutable cut records too, without changing IDs, ownership or stored defects.
function cardOutline(d,geo,defects){
 const [bx,by,ex,ey]=geo.bounds,w=ex-bx,h=ey-by,rad=d.corners==='round'?3.18:0;
 let corners=geo.corners||[rad,rad,rad,rad],notches=[[],[],[],[]];
 if(geo.factory&&rad){
  const cells=geo.cells||[1],cols=cells.map(c=>(c-1)%4),rows=cells.map(c=>Math.floor((c-1)/4));
  const left=Math.min(...cols),right=Math.max(...cols)+1,top=Math.min(...rows),bottom=Math.max(...rows)+1;
  const cuts=new Map((defects.cuts||[]).map(c=>[c.axis+':'+c.line,c]));
  corners=[['tl',left,top],['tr',right,top],['bl',left,bottom],['br',right,bottom]].map(([q,x,y])=>{
   const outside=(x===0||x===4)&&(y===0||y===4),loss=outside?cuts.get(q+':0'):null;
   return loss?.missing?0:Math.max(0,rad+(loss?.offset||0));
  });
  // Only an effective crossing cut removes these edge sectors. A missing
  // vertical/horizontal line retains the scraps at the two sheet boundaries.
  if(top>0)for(let x=left+1;x<right;x++)notches[0].push(x*d.width_mm-bx);
  if(right<4)for(let y=top+1;y<bottom;y++)notches[1].push(y*d.height_mm-by);
  if(bottom<4)for(let x=right-1;x>left;x--)notches[2].push(ex-x*d.width_mm);
  if(left>0)for(let y=bottom-1;y>top;y--)notches[3].push(ey-y*d.height_mm);
 }
 corners=corners.map(r=>clamp(r,0,Math.min(w,h)/2));
 const points=[],put=(x,y,side)=>points.push(side===0?[x,y]:side===1?[w-y,x]:side===2?[w-x,h-y]:[y,h-x]);
 // Traverse clockwise: a top edge, its notches, then its ending rounded corner.
 for(let side=0;side<4;side++){
  const length=side%2?h:w,start=[corners[0],corners[1],corners[3],corners[2]][side],end=[corners[1],corners[3],corners[2],corners[0]][side];
  put(start,0,side);
  for(const at of notches[side]){
   const r=Math.min(rad,at-start,length-end-at);if(r<=0)continue;
   put(at-r,0,side);
   for(let i=1;i<=8;i++){const a=-Math.PI/2+i*Math.PI/16;put(at-r+Math.cos(a)*r,r+Math.sin(a)*r,side)}
   for(let i=1;i<=8;i++){const a=Math.PI+i*Math.PI/16;put(at+r+Math.cos(a)*r,r+Math.sin(a)*r,side)}
  }
  put(length-end,0,side);
  if(end)for(let i=1;i<=10;i++){const a=-Math.PI/2+i*Math.PI/20;put(length-end+Math.cos(a)*end,end+Math.sin(a)*end,side)}
 }
 return points;
}
function outlinePath(ctx,points){ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.closePath()}
const visible=new Set();
const observer=new IntersectionObserver(entries=>{for(const e of entries){const r=e.target._ycRenderer;if(r){r.visible=e.isIntersecting;if(r.visible){visible.add(r);r.draw()}else visible.delete(r)}}},{rootMargin:'80px'});
class Renderer{
 constructor(host,data,{interactive=true,onSelect=null,onMove=null}={}){
  this.host=host;this.data=data;this.interactive=interactive;this.onSelect=onSelect;this.onMove=onMove;this.rx=0;this.ry=0;this.visible=true;this.disposed=false;
  this.canvas=document.createElement('canvas');this.canvas.className='yc-canvas';this.canvas.setAttribute('aria-label',data.name||data.design.name||'卡片预览');this.canvas.setAttribute('role','img');host.append(this.canvas);host.classList.add('card-stage');host._ycRenderer=this;
  this.abort=new AbortController();const opts={signal:this.abort.signal};
  host.addEventListener('pointerdown',e=>this.down(e),opts);host.addEventListener('pointermove',e=>this.move(e),opts);host.addEventListener('pointerup',e=>this.up(e),opts);host.addEventListener('pointercancel',e=>this.up(e),opts);host.addEventListener('pointerleave',()=>{if(!this.pointer&&!this.pinned){this.rx=this.ry=0;this.schedule()}},opts);
  observer.observe(host);this.resize=new ResizeObserver(()=>this.draw());this.resize.observe(host);this.load();
 }
 set(data){this.data=data;this.load()}
 load(){const all=[];for(const l of this.data.design.layers){for(const [s,b] of [[l.image,false],[l.mask,true],[l.pattern,true],[l.repeat_mask?.pattern,true]]){const t=texture(s,b);if(t)all.push(t.promise)}}this.ready=Promise.all(all).then(()=>this.schedule());this.schedule()}
 destroy(){this.disposed=true;observer.unobserve(this.host);visible.delete(this);this.resize.disconnect();this.abort.abort();cancelAnimationFrame(this.frame);this.host._ycRenderer=null;this.canvas.remove();this.edgeCanvas?.remove();this.backdropCanvas?.remove();this.backgroundPlanes?.forEach(p=>p.remove())}
 down(e){if(e.button&&e.button!==0)return;
  if(this.interactive&&e.pointerType==='touch'&&!this.pinned&&!this.gyro){const rect=this.canvas.getBoundingClientRect();this.ry=(e.clientX-rect.left-rect.width/2)/Math.max(rect.width/2,1)*26;this.rx=-(e.clientY-rect.top-rect.height/2)/Math.max(rect.height/2,1)*26;this.limit();this.schedule()}
  this.pointer={x:e.clientX,y:e.clientY,rx:this.rx,ry:this.ry,moved:false};if(this.interactive||this.onMove)this.host.setPointerCapture(e.pointerId)
 }
 move(e){const rect=this.canvas.getBoundingClientRect();if(!this.interactive){if(this.pointer&&this.onMove){const d=this.data.design;this.pointer.moved=true;this.onMove((e.clientX-this.pointer.x)/Math.max(rect.width,1)*d.width_mm,(e.clientY-this.pointer.y)/Math.max(rect.height,1)*d.height_mm);this.pointer.x=e.clientX;this.pointer.y=e.clientY}return}
 if(this.pointer){let dx=e.clientX-this.pointer.x,dy=e.clientY-this.pointer.y;if(Math.abs(dx)+Math.abs(dy)>5)this.pointer.moved=true;this.ry=this.pointer.ry+dx*.6;this.rx=this.pointer.rx-dy*.6;this.pinned=true}
 else {this.ry=(e.clientX-rect.left-rect.width/2)/Math.max(rect.width/2,1)*26;this.rx=-(e.clientY-rect.top-rect.height/2)/Math.max(rect.height/2,1)*26;this.pinned=false}
 this.limit();this.schedule();
 }
 up(e){if(!this.pointer)return;const moved=this.pointer.moved;this.pointer=null;if(this.host.hasPointerCapture(e.pointerId))this.host.releasePointerCapture(e.pointerId);if(!moved&&this.onSelect)this.onSelect(e)}
 limit(){if(this.data.design.material==='plastic'){this.rx=clamp(this.rx,-90,90);this.ry=clamp(this.ry,-90,90)}else{this.rx=((this.rx+540)%360)-180;this.ry=((this.ry+540)%360)-180}}
 sensor(x,y){this.rx=x;this.ry=y;this.gyro=true;this.limit();this.schedule()}
 reset(){this.rx=this.ry=0;this.pinned=false;this.gyro=false;this.schedule()}
 schedule(){if(this.disposed||this.suspended||!this.visible||this.frame)return;this.frame=requestAnimationFrame(()=>{this.frame=null;this.draw()})}
 draw(){if(this.disposed||this.suspended||!this.visible||!this.host.isConnected)return;
  const d=this.data.design,w=d.width_mm,h=d.height_mm, geo=this.data.geometry||{bounds:[0,0,w,h],cells:[1],corners:[d.corners==='round'?3.18:0,d.corners==='round'?3.18:0,d.corners==='round'?3.18:0,d.corners==='round'?3.18:0]};
  const [bx,by,ex,ey]=geo.bounds, gw=ex-bx,gh=ey-by;
  // Interpret persisted cut records consistently for new and legacy products.
  const defects=this.data.defects||{layers:[],cuts:[]},outline=cardOutline(d,geo,defects);
  const radii=geo.corners;
  const complexity=d.layers.length;const quality=complexity>120?.5:complexity>50?.75:1;
  const longest=(this.host.closest('.inspector')?1200:this.host.closest('.editor-stage')?960:600)*quality;
  const scale=Math.min(longest/Math.max(gw,gh),Math.max(1,devicePixelRatio)*Math.max(this.host.clientWidth,200)/gw);
  const cw=Math.max(1,Math.round(gw*scale)),ch=Math.max(1,Math.round(gh*scale));
  if(this.canvas.width!==cw||this.canvas.height!==ch){this.canvas.width=cw;this.canvas.height=ch}
  this.canvas.style.aspectRatio=`${gw}/${gh}`;
  this.canvas.style.transform=this.gyro?'none':`perspective(1200px) rotateX(${this.rx}deg) rotateY(${this.ry}deg)`;
  this.scratch('backdropCanvas');this.hasBackdrop=false;this.backgroundPasses=[];
  const ctx=this.canvas.getContext('2d');ctx.clearRect(0,0,cw,ch);ctx.save();ctx.scale(scale,scale);
  const back=d.material==='glass'&&Math.cos(this.rx*Math.PI/180)*Math.cos(this.ry*Math.PI/180)<0;
  outlinePath(ctx,outline);ctx.clip();

  ctx.fillStyle=d.material==='plastic'?'#030406':'rgba(166,215,243,.12)';ctx.fillRect(0,0,gw,gh);
  const viewAngle=a=>clamp(Math.atan2(Math.sin(a*Math.PI/180),Math.abs(Math.cos(a*Math.PI/180))),-Math.PI/4,Math.PI/4);
  const ax=viewAngle(this.ry),ay=viewAngle(this.rx);
  const misses=new Map((defects.layers||[]).map(l=>[l.id,l]));
  const glass=d.material==='glass',face=back?-1:1;
  const depth=l=>(l.position==='front'?1:l.position==='behind'?-1:0)*l.distance_mm*(l.side==='back'?-1:1);
  const surface=l=>depth(l)+(glass?(l.side==='back'?-.0001:.0001):0);
  const ordered=d.layers.filter(l=>!misses.get(l.id)?.missing&&(glass?(l.type==='image'||l.position==='surface'||(l.side==='back'?-1:1)*face>0):l.side==='front')).slice().sort((a,b)=>(surface(a)-surface(b))*face||a.order-b.order);
  // Consecutive negative filters are involutions. Identical masks/transforms
  // cancel in pairs without dropping any design layers or changing their cost.
  const layers=[];let negatives=new Map();const flush=()=>{layers.push(...negatives.values());negatives.clear()};
  for(const l of ordered){if(l.type==='filter'&&l.effect==='negative'){
   const signature=JSON.stringify([l.side,l.mask,l.repeat_mask,l.x,l.y,l.width,l.height,l.rotation,depth(l),misses.get(l.id)?.offset]);
   if(negatives.has(signature))negatives.delete(signature);else negatives.set(signature,l);
  }else{flush();layers.push(l)}}flush();
  // Compose the sheet at nominal grid positions, then crop to actual cut bounds.
  // Neighbours may print into a shifted cut, so draw all 16 cells for a factory product.
  const sheet=!!this.data.geometry&&Math.max(...(geo.cells||[1]))>1||this.data.geometry?.factory;
  const cells=sheet?Array.from({length:16},(_,i)=>i+1):(geo.cells||[1]);
  for(const cell of cells){const col=(cell-1)%4,row=Math.floor((cell-1)/4), ox=col*w-bx,oy=row*h-by;
   if(ox+w<=0||oy+h<=0||ox>=gw||oy>=gh)continue;
   ctx.save();ctx.beginPath();ctx.rect(ox,oy,w,h);ctx.clip();

   for(const l of layers){const loss=misses.get(l.id);if(loss?.missing)continue;
    this.layer(ctx,l,{w,h,ox,oy,ax,ay,scale,gw,gh,back,depth:depth(l),offset:loss?.offset||[0,0],mono:d.color==='mono',absoluteX:sheet?col*w:0,absoluteY:sheet?row*h:0});
   }
   ctx.restore();
  }
  ctx.restore();this.syncBackdrop();this.edges(gw,gh,radii,outline);
 }
 repeatMask(l,h){
  if(l.repeat_mask)return l.repeat_mask;
  if(l.type==='foil'&&['text','pattern'].includes(l.effect))return {kind:l.pattern?'pattern':'text',pattern:l.pattern,text:l.text||'YCARDS',density:l.density||6,angle:l.angle??-25,size_mm:h/(l.density||6)*.55,pattern_width_mm:h/(l.density||6)*2,font:'sans',spacing_mm:3,x:0,y:0};
  return null;
 }
 scratch(name){
  const c=this[name]||(this[name]=document.createElement('canvas'));
  if(c.width!==this.canvas.width||c.height!==this.canvas.height){c.width=this.canvas.width;c.height=this.canvas.height}
  const x=c.getContext('2d');x.setTransform(1,0,0,1,0,0);x.globalCompositeOperation='source-over';x.globalAlpha=1;x.filter='none';x.clearRect(0,0,c.width,c.height);return [c,x];
 }
 patternTile(p,h,scale){
  const signature=JSON.stringify([p,h,Math.round(scale*100)]);
  if(this.patternCache?.has(signature))return this.patternCache.get(signature);
  const c=document.createElement('canvas'),x=c.getContext('2d');
  const size=p.size_mm||6,font={sans:'sans-serif',serif:'serif',mono:'monospace'}[p.font]||'sans-serif';
  x.font=`bold ${size*scale}px ${font}`;
  const content=p.kind==='pattern'?(p.pattern_width_mm||size*2):x.measureText(p.text||'YCARDS').width/scale;
  const span=Math.max(.5,content+(p.spacing_mm??3)),gap=h/(p.density||6);
  // One cell can contain overflowing tall glyphs, preserving independent size/density.
  const ratio=Math.min(scale,2048/span,2048/gap);
  c.width=Math.max(1,Math.ceil(span*ratio));c.height=Math.max(1,Math.ceil(gap*ratio));
  x.scale(c.width/span,c.height/gap);x.fillStyle='#fff';x.font=`bold ${size}px ${font}`;x.textBaseline='middle';
  const im=p.kind==='pattern'?texture(p.pattern,true)?.image:null;
  if(p.kind==='pattern'&&!im)return null;
  for(let y=-Math.ceil(size/gap)*gap;y<gap+size;y+=gap){if(im)x.drawImage(im,0,y-size/2,content,size);else x.fillText(p.text||'YCARDS',0,y)}
  if(!this.patternCache)this.patternCache=new Map();if(this.patternCache.size>12)this.patternCache.delete(this.patternCache.keys().next().value);
  const tile={canvas:c,span,gap};this.patternCache.set(signature,tile);return tile;
 }
 applyMasks(t,l,g,x,y){
  const {scale,h,ox,oy,absoluteX,absoluteY}=g,lw=l.width,lh=l.height;
  const masks=[];
  if(l.mask)masks.push('custom');
  const repeat=this.repeatMask(l,h);if(repeat)masks.push('repeat');
  for(const kind of masks){
   const [mc,m]=this.scratch('maskCanvas');m.scale(scale,scale);
   if(kind==='custom'){
    const im=texture(l.mask,true)?.image;if(!im)return false;
    m.translate(x,y);m.rotate((l.side==='back'?-1:1)*l.rotation*Math.PI/180);if(l.side==='back')m.scale(-1,1);m.drawImage(im,-lw/2,-lh/2,lw,lh);
   }else{
    const tile=this.patternTile(repeat,h,scale);if(!tile)return false;
    const pattern=m.createPattern(tile.canvas,'repeat');
    pattern.setTransform(new DOMMatrix().scale(tile.span/tile.canvas.width,tile.gap/tile.canvas.height));
    // A sheet-wide printing origin: neighbouring cards crop different phases.
    m.translate(x+(repeat.x||0)-absoluteX,y+(repeat.y||0)-absoluteY);
    if(l.side==='back')m.scale(-1,1);m.rotate((l.rotation+(repeat.angle||0))*Math.PI/180);m.fillStyle=pattern;
    const extent=3000;m.fillRect(-extent,-extent,extent*2,extent*2);
   }
   t.save();t.setTransform(1,0,0,1,0,0);t.globalCompositeOperation='destination-in';t.drawImage(mc,0,0);t.restore();
  }
  return true;
 }
 bevel(t,tmp,scale,ax,ay){
  // Raised material edges: opposing highlights/shadows follow the illumination.
  const [mask,m]=this.scratch('bevelMask');m.drawImage(tmp,0,0);
  const [edge,e]=this.scratch('bevelCanvas');
  const px=Math.max(1,scale*.16),dx=Math.cos(ax*2+.7)*px,dy=Math.sin(ay*2+.7)*px;
  for(const [sign,color] of [[1,'rgba(255,255,255,.8)'],[-1,'rgba(0,0,0,.7)']]){
   e.clearRect(0,0,edge.width,edge.height);e.globalCompositeOperation='source-over';e.drawImage(mask,0,0);
   e.globalCompositeOperation='destination-out';e.drawImage(mask,dx*sign,dy*sign);
   e.globalCompositeOperation='source-in';e.fillStyle=color;e.fillRect(0,0,edge.width,edge.height);
   t.save();t.setTransform(1,0,0,1,0,0);t.globalCompositeOperation='source-atop';t.drawImage(edge,0,0);t.restore();
  }
 }
 glow(t,l,lw,lh,ax,ay){
  const color=l.tint||'#bdeaff',opacity=l.opacity??1,feather=l.feather??.65,width=l.glow_width||4;
  const rgb=[1,3,5].map(i=>parseInt(color.slice(i,i+2),16));
  const rgba=a=>`rgba(${rgb.join(',')},${a*opacity})`;
  const span=Math.hypot(lw,lh);
  if(l.effect==='ring'||l.effect==='circle'){
   const radius=Math.max(.1,Math.min(lw,lh)*(.3+.23*Math.sin(ax*2+ay*1.5))),outer=radius+width/2,inner=Math.max(0,radius-width/2);
   const grad=t.createRadialGradient(0,0,0,0,0,outer);const soften=Math.min(width/2,width*feather/2),eps=.00001;
   grad.addColorStop(0,rgba(inner<=0?1:0));
   grad.addColorStop(Math.max(0,inner/outer-eps),rgba(0));
   grad.addColorStop(Math.min(1,(inner+soften)/outer),rgba(1));
   grad.addColorStop(Math.max((inner+soften)/outer,(outer-soften)/outer),rgba(1));
   grad.addColorStop(1,rgba(0));t.fillStyle=grad;t.fillRect(-lw/2,-lh/2,lw,lh);
  }else if(l.effect==='bar'){
   const at=Math.sin(ax*2+ay)*lw*.45;t.rotate(-.35);
   const grad=t.createLinearGradient(at-width/2,0,at+width/2,0),edge=Math.max(.00001,feather/2);
   grad.addColorStop(0,rgba(0));grad.addColorStop(edge,rgba(1));grad.addColorStop(1-edge,rgba(1));grad.addColorStop(1,rgba(0));t.fillStyle=grad;t.fillRect(at-width/2,-span,width,span*2);
  }else{
   t.rotate(ax*3+ay*2);const rays=l.effect==='star'?4:2;
   for(let n=0;n<rays;n++){t.save();t.rotate(n*Math.PI*2/rays);
    const grad=t.createLinearGradient(0,-width/2,0,width/2),edge=Math.max(.00001,feather/2);
    grad.addColorStop(0,rgba(0));grad.addColorStop(edge,rgba(1));grad.addColorStop(1-edge,rgba(1));grad.addColorStop(1,rgba(0));
    t.fillStyle=grad;t.fillRect(0,-width/2,span,width);t.restore();
   }
  }
 }
 layer(ctx,l,g){
  const {w,h,ox,oy,ax,ay,scale,back,depth,offset,mono}=g;
  // CSS rotationY moves positive z right; rotationX moves it upward.
  const dx=Math.tan(ax)*depth,dy=-Math.tan(ay)*depth;
  const mirrored=l.side==='back';
  const x=ox+w/2+(mirrored?-l.x:l.x)+offset[0]+dx,y=oy+h/2+l.y+offset[1]+dy,lw=l.width,lh=l.height;
  const [tmp,t]=this.scratch('effectCanvas');t.scale(scale,scale);
  t.save();t.translate(ox+w/2+dx,oy+h/2+dy);t.beginPath();t.rect(-w/2-Math.abs(depth),-h/2-Math.abs(depth),w+2*Math.abs(depth),h+2*Math.abs(depth));t.clip();t.translate(x-(ox+w/2+dx),y-(oy+h/2+dy));t.rotate((mirrored?-1:1)*l.rotation*Math.PI/180);if(mirrored)t.scale(-1,1);
  t.beginPath();t.rect(-lw/2,-lh/2,lw,lh);t.clip();
  if(l.type==='image'){
   const im=texture(l.image)?.image;if(im){if(mono)t.filter='grayscale(1)';t.drawImage(im,-lw/2,-lh/2,lw,lh)}
  }else{
   const shine=(Math.sin(ax*2+ay)+1)/2;
   if(l.type==='filter'){t.fillStyle='white';t.fillRect(-lw/2,-lh/2,lw,lh)}
   else if(l.type==='material'){
    const stops=l.effect==='gold'?['#5b3911','#fff0a6','#bb8424','#ffeac0']:l.effect==='silver'?['#444f63','#f3ffff','#889ab0','#fff']:['#040405','#242428','#070708','#161619'];
    const grad=t.createLinearGradient(-lw+shine*lw,-lh/2,lw/2+shine*lw,lh/2);stops.forEach((c,i)=>grad.addColorStop(i/3,c));t.fillStyle=grad;t.fillRect(-lw/2,-lh/2,lw,lh);
   }else if(l.type==='foil'){
    const colors=l.effect==='mono'?['#313131','#c7c7c7','#ffffff','#676767','#ececec']:['#42dbff','#c268ee','#ffe98c','#56f6c9','#ec79ed'];
    const grad=t.createLinearGradient(-lw+shine*lw,-lh,lw,lh);colors.forEach((c,i)=>grad.addColorStop(i/4,c));t.fillStyle=grad;t.fillRect(-lw/2,-lh/2,lw,lh);
   }else if(l.type==='glow')this.glow(t,l,lw,lh,ax,ay);
  }
  t.restore();
  if(l.type!=='image'&&!this.applyMasks(t,l,g,x,y))return;
  if(l.type==='material')this.bevel(t,tmp,scale,ax,ay);
  ctx.save();
  if(l.type==='filter'){
   const [fc,f]=this.scratch('filterCanvas');
   if(l.effect==='lenticular'){
    this.captureBackgroundPass();
    this.backgroundPasses.push({kind:'blur',mask:this.filterArea(tmp,g).toDataURL(),blur:Math.max(.2,(l.strength??.65)*scale*.45)});
    const [source,s]=this.scratch('filterSource');s.filter=`blur(${Math.max(.2,(l.strength??.65)*scale*.45)}px)`;s.drawImage(this.canvas,0,0);s.filter='none';
    const pitch=Math.max(1,(l.pitch_mm||.7)*scale),strength=l.strength??.65;
    // Cylindrical ridges distort/soften the already-rendered backdrop.
    for(let at=0;at<fc.width;at+=pitch){
     const shift=Math.sin(ax*2+at/pitch*.12)*pitch*.16*strength;
     const sx=clamp(at+shift,0,source.width-1),sw=Math.min(pitch,source.width-sx);
     f.drawImage(source,sx,0,sw,source.height,at,0,Math.min(pitch,fc.width-at),fc.height);
    }
    f.globalCompositeOperation='source-atop';
    for(let at=0;at<fc.width;at+=pitch){
     const grad=f.createLinearGradient(at,0,at+pitch,0);grad.addColorStop(0,`rgba(0,0,0,${.25*strength})`);grad.addColorStop(.3,`rgba(255,255,255,${(.2+Math.sin(ax)*.1)*strength})`);grad.addColorStop(.6,'rgba(255,255,255,0)');grad.addColorStop(1,`rgba(0,0,0,${.15*strength})`);f.fillStyle=grad;f.fillRect(at,0,pitch,fc.height);
    }
   }else{
    f.filter='invert(1)';f.drawImage(this.canvas,0,0);f.filter='none';
    // Inversion is affine: invert foreground RGB while retaining its alpha,
    // and invert the real backdrop behind it. XOR tracks successive inversions.
    const bd=this.backdropCanvas.getContext('2d');bd.globalCompositeOperation='xor';bd.drawImage(this.filterArea(tmp,g),0,0);this.hasBackdrop=true;
   }
   f.globalCompositeOperation='destination-in';f.setTransform(1,0,0,1,0,0);f.drawImage(tmp,0,0);
   ctx.setTransform(1,0,0,1,0,0);ctx.globalCompositeOperation='destination-out';ctx.drawImage(tmp,0,0);
   ctx.setTransform(1,0,0,1,0,0);ctx.globalCompositeOperation='source-over';ctx.drawImage(fc,0,0);
  }else{
   ctx.setTransform(1,0,0,1,0,0);
   if(mono&&l.type!=='image')ctx.filter='grayscale(1)';ctx.drawImage(tmp,0,0);
  }
  ctx.restore();
 }
 filterArea(tmp,g){
  const {w,h,ox,oy,scale}=g,d=this.data.design;
  const [mask,m]=this.scratch('backdropCutMask');m.save();m.scale(scale,scale);
  const geo=this.data.geometry||{bounds:[0,0,w,h],corners:Array(4).fill(d.corners==='round'?3.18:0)};
  outlinePath(m,cardOutline(d,geo,this.data.defects||{}));m.clip();m.beginPath();m.rect(ox,oy,w,h);m.clip();m.setTransform(1,0,0,1,0,0);m.drawImage(tmp,0,0);m.restore();return mask;
 }
 captureBackgroundPass(){
  if(!this.hasBackdrop)return;
  const c=document.createElement('canvas');c.width=this.canvas.width;c.height=this.canvas.height;c.getContext('2d').drawImage(this.backdropCanvas,0,0);
  this.backgroundPasses.push({kind:'negative',canvas:c});
  this.backdropCanvas.getContext('2d').clearRect(0,0,c.width,c.height);this.hasBackdrop=false;
 }
 syncBackdrop(){
  // The foreground canvas retains alpha. These ordered passes filter the actual
  // page behind it, including another card, without sampling or uploading the page.
  const passes=this.backgroundPasses.slice();if(this.hasBackdrop)passes.push({kind:'negative',canvas:this.backdropCanvas});
  this.canvas.style.filter=passes.length?'none':'';
  if(!this.backgroundPlanes)this.backgroundPlanes=[];
  while(this.backgroundPlanes.length>passes.length)this.backgroundPlanes.pop().remove();
  passes.forEach((pass,i)=>{
   let c=this.backgroundPlanes[i];if(!c){c=document.createElement('canvas');c.setAttribute('aria-hidden','true');this.host.insertBefore(c,this.canvas);this.backgroundPlanes.push(c)}
   c.width=this.canvas.width;c.height=this.canvas.height;
   c.style.cssText='position:absolute;pointer-events:none;z-index:1;';
   c.style.left=this.canvas.offsetLeft+'px';c.style.top=this.canvas.offsetTop+'px';c.style.width=this.canvas.offsetWidth+'px';c.style.height=this.canvas.offsetHeight+'px';c.style.transform=this.canvas.style.transform;
   if(pass.kind==='negative'){c.getContext('2d').drawImage(pass.canvas,0,0);c.style.mixBlendMode='difference'}
   else{const blur=pass.blur*this.canvas.offsetWidth/this.canvas.width;c.style.backdropFilter=`blur(${blur}px)`;c.style.webkitBackdropFilter=c.style.backdropFilter;c.style.maskImage=`url("${pass.mask}")`;c.style.maskSize='100% 100%';c.style.webkitMaskImage=c.style.maskImage;c.style.webkitMaskSize='100% 100%'}
  });
 }
 edges(gw,gh,radii,outline){
  if(!this.edgeCanvas){this.edgeCanvas=document.createElement('canvas');this.edgeCanvas.className='yc-edge';this.edgeCanvas.setAttribute('aria-hidden','true');this.host.prepend(this.edgeCanvas)}
  const c=this.edgeCanvas,ratio=Math.min(2,devicePixelRatio||1),hw=this.host.clientWidth,hh=this.host.clientHeight;
  c.width=Math.max(1,Math.round(hw*ratio));c.height=Math.max(1,Math.round(hh*ratio));
  c.style.width=hw+'px';c.style.height=hh+'px';
  const e=c.getContext('2d');e.scale(ratio,ratio);if(this.gyro)return;
  const size=this.canvas.offsetWidth/gw,px=this.canvas.offsetLeft+this.canvas.offsetWidth/2,py=this.canvas.offsetTop+this.canvas.offsetHeight/2;
  const rx=this.rx*Math.PI/180,ry=this.ry*Math.PI/180,thick=.76*size;
  const project=(x,y,z)=>{const xx=x*Math.cos(ry)+z*Math.sin(ry),zz=-x*Math.sin(ry)+z*Math.cos(ry),yy=y*Math.cos(rx)-zz*Math.sin(rx),depth=y*Math.sin(rx)+zz*Math.cos(rx),factor=1200/(1200-depth);return [px+xx*factor,py+yy*factor]};
  const points=outline.map(([x,y])=>[(x-gw/2)*size,(y-gh/2)*size]);
  const glass=this.data.design.material==='glass';
  for(let i=0;i<points.length;i++){
   const a=points[i],b=points[(i+1)%points.length],nx=b[1]-a[1],ny=a[0]-b[0];
   const facing=-nx*Math.sin(ry)*Math.cos(rx)+ny*Math.sin(rx);
   if(facing<=0)continue;
   const quad=[project(...a,0),project(...b,0),project(...b,-thick),project(...a,-thick)];
   e.beginPath();quad.forEach(([x,y],j)=>j?e.lineTo(x,y):e.moveTo(x,y));e.closePath();
   e.fillStyle=glass?'rgba(176,226,246,.2)':'#020304';e.fill();
   if(glass){e.strokeStyle='rgba(205,245,255,.32)';e.lineWidth=.3;e.stroke()}
  }
 }

}
window.YCardRenderer=Renderer;
window.ycBinaryMask=async function(src){const t=texture(src,true);const im=await t.promise;if(!im)throw Error('无法读取遮罩');const c=document.createElement('canvas');c.width=im.width;c.height=im.height;c.getContext('2d').drawImage(im,0,0);return c.toDataURL('image/png')};
window.ycRotationLimit=(material,x,y)=>material==='plastic'?[clamp(x,-90,90),clamp(y,-90,90)]:[x,y];
})();
