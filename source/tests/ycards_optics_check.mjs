import fs from 'node:fs';
const pages=await(await fetch('http://127.0.0.1:9225/json')).json(),ws=new WebSocket(pages[0].webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));let seq=0;const pending=new Map();const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}});
const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value};
await send('Page.enable');await send('Runtime.enable');await send('Emulation.setDeviceMetricsOverride',{width:1000,height:900,deviceScaleFactor:1,mobile:false});await send('Page.navigate',{url:'http://127.0.0.1:5079/ycards/'});await new Promise(r=>setTimeout(r,600));
const checks=await evaluate(`(async()=>{
const checks=[],assert=(name,ok,detail)=>{checks.push({name,ok,detail});if(!ok)throw Error(name+': '+JSON.stringify(detail))};
const host=document.createElement('div');host.id='optics-test';host.style='position:fixed;left:0;top:0;width:400px;height:640px;z-index:9000;background:rgb(40,80,120);isolation:isolate';document.body.append(host);
const d={material:'glass',name:'Optics',width_mm:53.98,height_mm:85.6,corners:'square',color:'color',layers:[]};
const l=(type,extra={})=>({id:Math.random().toString(),type,effect:type==='image'?'image':type==='material'?'gold':'negative',side:'front',position:'surface',distance_mm:0,x:0,y:0,width:d.width_mm,height:d.height_mm,rotation:0,order:0,...extra});
const png=color=>{const c=document.createElement('canvas');c.width=c.height=10;const x=c.getContext('2d');x.fillStyle=color;x.fillRect(0,0,10,10);return c.toDataURL()};
const r=new YCardRenderer(host,{design:d},{interactive:false});window.optics={r,d,host};r.visible=true;
const set=async()=>{r.set({design:d});await new Promise(s=>setTimeout(s,50));r.draw()};
const pixel=(x=.5,y=.5)=>[...r.canvas.getContext('2d').getImageData(Math.floor(r.canvas.width*x),Math.floor(r.canvas.height*y),1,1).data];
const red=png('#ff0000'),blue=png('#0000ff');
d.layers=[l('image',{image:red,position:'front',distance_mm:8}),l('image',{image:blue,position:'behind',distance_mm:8})];await set();const front=pixel();r.ry=180;r.draw();const back=pixel();assert('glass reverses physical image occlusion when flipped',front[0]===255&&back[2]===255,{front,back});
d.layers=[l('image',{image:red,position:'front',distance_mm:8})];await set();assert('front image remains visible through back',pixel()[0]===255,pixel());
d.layers=[l('material',{position:'front',distance_mm:0})];await set();assert('far effect hidden even with zero front distance',pixel()[3]<40,pixel());
d.layers=[l('material')];await set();assert('surface material visible on reverse',pixel()[3]===255,pixel());
r.ry=0;d.layers=[l('material')];await set();const gold=pixel();d.layers.push(l('filter',{order:1}));await set();const inverted=pixel();assert('negative inverts material beneath',gold.slice(0,3).every((v,i)=>Math.abs(255-v-inverted[i])<3),{gold,inverted});
d.layers=[l('image',{image:red}),l('filter',{order:1})];await set();assert('negative inverts image beneath',pixel()[0]<3&&pixel()[1]>250&&pixel()[2]>250,pixel());
d.layers=[l('filter')];await set();const single=pixel(),bd=()=>[...r.backdropCanvas.getContext('2d').getImageData(r.canvas.width/2,r.canvas.height/2,1,1).data];assert('transparent glass carries real background inversion',single[3]<40&&bd()[3]===255,{single,backdrop:bd()});
d.layers.push(l('filter',{order:1}));await set();assert('two negatives cancel background inversion',bd()[3]===0,bd());
d.layers=[l('filter',{effect:'lenticular',strength:1})];await set();assert('glass lenticular filters real page background',r.backgroundPlanes.some(p=>p.style.backdropFilter.includes('blur')&&p.style.maskImage.includes('data:image/png')));
d.layers=[l('filter'),l('filter',{effect:'lenticular',strength:1,order:1}),l('filter',{order:2})];await set();assert('background filter passes preserve negative/lens/negative order',r.backgroundPlanes.length===3&&r.backgroundPlanes[0].style.mixBlendMode==='difference'&&r.backgroundPlanes[1].style.backdropFilter.includes('blur')&&r.backgroundPlanes[2].style.mixBlendMode==='difference');
// Missing full-sheet cut: top sheet edge retains scrap; interior crossing removes it.
d.material='plastic';d.corners='round';d.layers=[l('image',{image:red})];
const defects={layers:[],cuts:[{axis:'x',line:1,missing:true},{axis:'tl',line:0,missing:true}]};
r.set({design:d,geometry:{cells:[1,2],factory:true,bounds:[0,0,107.96,85.6],corners:[0,3.18,3.18,3.18]},defects});await new Promise(s=>setTimeout(s,50));r.draw();
assert('missing outer corner leaves only that corner square',pixel(.003,.003)[3]>200&&pixel(.997,.003)[3]===0);
assert('missing vertical cut retains scrap at sheet top and joins cells',pixel(.5,.01)[3]===255,pixel(.5,.01));
assert('effective horizontal cut removes scrap at connected seam',pixel(.5,.99)[3]===0,pixel(.5,.99));
r.set({design:d,geometry:{cells:[5,6],factory:true,bounds:[0,85.6,107.96,171.2],corners:[0,3.18,3.18,3.18]},defects});r.draw();assert('legacy global corner loss no longer squares interior cell corner',pixel(.003,.003)[3]===0);
// Leave transparent inversion for an actual browser screenshot, not canvas-only checks.
d.material='glass';d.corners='square';d.layers=[l('filter')];await set();
return checks;
})()`);
await new Promise(r=>setTimeout(r,100));fs.mkdirSync('tests/ycards-optics-artifacts',{recursive:true});const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync('tests/ycards-optics-artifacts/background-negative.png',Buffer.from(shot.data,'base64'));
console.log(JSON.stringify(checks,null,2));fs.writeFileSync('tests/ycards-optics-artifacts/checks.json',JSON.stringify(checks,null,2));ws.close();
