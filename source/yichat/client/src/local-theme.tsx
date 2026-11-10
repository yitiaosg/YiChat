import {useEffect,useRef,useState} from 'preact/hooks';
import {isApp,appCall} from './app';
import {chat} from './chat';
import {Modal,ErrorText} from './shared';
import {ColorEditor} from './color-editor';
import {ImagePlus,RotateCcw,Trash2} from 'lucide-preact';
type Appearance={color?:string;background?:string};
const valid=(v:unknown):v is string=>typeof v==='string'&&/^#[0-9a-f]{6}$/i.test(v);
const key=(room='')=>'yc:appearance:'+chat.session.uid+':'+room;
function read(room=''):Appearance{try{return JSON.parse(localStorage.getItem(key(room))||'{}')}catch{return {}}}
let systemColor='#1479b8';
// The account is unknown while the app loads; remember the last applied choice on this device.
const LAST='yc:appearance:last';
function remembered(){try{const color=localStorage.getItem(LAST);return valid(color)?color:undefined}catch{return undefined}}
function palette(color:string,node:HTMLElement){
 const rgb=[1,3,5].map(i=>parseInt(color.slice(i,i+2),16)/255),max=Math.max(...rgb),min=Math.min(...rgb),delta=max-min,light=(max+min)/2;let hue=0;
 if(delta){if(max===rgb[0])hue=((rgb[1]-rgb[2])/delta)%6;else if(max===rgb[1])hue=(rgb[2]-rgb[0])/delta+2;else hue=(rgb[0]-rgb[1])/delta+4;hue=(hue*60+360)%360}
 const saturation=delta?delta/(1-Math.abs(2*light-1)):0;
 node.style.setProperty('--logo-filter',`hue-rotate(${hue-205}deg) saturate(${saturation/.76})`);
const dark=document.documentElement.dataset.theme==='dark';const mix=(base:string,n:number)=>`color-mix(in srgb, ${color} ${n}%, ${base})`;const base=dark?'#101820':'#ffffff';for(const [k,v] of Object.entries({'accent':mix(dark?'white':'black',dark?65:80),'accent-soft':mix(base,dark?22:12),'sent':mix(base,dark?30:17),'bg':mix(base,dark?8:5),'panel':mix(base,dark?4:1),'sidebar':mix(base,7),'raised':mix(base,12),'border':mix(base,dark?25:18)}))node.style.setProperty('--'+k,v);node.style.setProperty('--theme-seed',color)}
// Paint the remembered colour before the first frame instead of the system colour.
if(isApp)try{const root=document.documentElement,color=remembered();if(!root.dataset.theme){const mode=localStorage.getItem('yc:theme')||'system';root.dataset.theme=mode==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):mode}if(color)palette(color,root)}catch{/* Fall back to the stylesheet colours. */}
export function useAppearance(room=''){
 const ref=useRef<HTMLElement>(null);
 useEffect(()=>{if(!isApp)return;const apply=()=>{const known=!!chat.session?.uid,global=known?read():{color:remembered()},local=room&&known?read(room):{},color=valid(local.color)?local.color:valid(global.color)?global.color:systemColor;if(known&&!room)try{if(valid(global.color))localStorage.setItem(LAST,global.color);else localStorage.removeItem(LAST)}catch{/* Storage is optional here. */}const node=room?ref.current:document.documentElement;if(!node)return;palette(color,node);if(room){node.style.backgroundImage=local.background?`url("${local.background}")`:'';node.classList.toggle('custom-chat-background',!!local.background)}{void appCall('setChromeColor',{color:getComputedStyle(node).getPropertyValue('--panel').trim(),seed:color,dark:document.documentElement.dataset.theme==='dark'}).catch(()=>{})}};apply();window.addEventListener('yc:appearance',apply);const observer=new MutationObserver(apply);observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});void appCall('systemThemeColor').then(r=>{if(valid(r?.color)){systemColor=r.color;apply()}}).catch(()=>{});return()=>{observer.disconnect();window.removeEventListener('yc:appearance',apply);if(!room)for(const k of ['accent','accent-soft','sent','bg','panel','sidebar','raised','border','theme-seed','logo-filter'])document.documentElement.style.removeProperty('--'+k)}} ,[room,chat.session?.uid]);return ref;
}
export function AppearanceSettings({room='',close}:{room?:string;close:()=>void}){
 const [value,setValue]=useState<Appearance>(read(room)),[suggest,setSuggest]=useState<string[]>([]),[error,setError]=useState('');const canvas=useRef<HTMLCanvasElement>(null),latest=useRef(value);latest.current=value;
 function save(next:Appearance){try{localStorage.setItem(key(room),JSON.stringify(next));setValue(next);window.dispatchEvent(new Event('yc:appearance'));setError('')}catch{setError('本机存储空间不足，请选择更小的背景图片。')}}
 async function background(file:File){try{if(!file.type.startsWith('image/'))throw Error('请选择图片');const bitmap=await createImageBitmap(file),c=document.createElement('canvas'),scale=Math.min(1,1600/Math.max(bitmap.width,bitmap.height));c.width=Math.round(bitmap.width*scale);c.height=Math.round(bitmap.height*scale);const ctx=c.getContext('2d')!;ctx.drawImage(bitmap,0,0,c.width,c.height);bitmap.close();const counts=new Map<string,number>(),pixels=ctx.getImageData(0,0,c.width,c.height).data;for(let i=0;i<pixels.length;i+=160){if(pixels[i+3]<128)continue;const rgb=[pixels[i],pixels[i+1],pixels[i+2]].map(x=>Math.min(255,Math.round(x/32)*32)),hex='#'+rgb.map(x=>x.toString(16).padStart(2,'0')).join('');counts.set(hex,(counts.get(hex)||0)+1)}const colors=[...counts].sort((a,b)=>b[1]-a[1]).slice(0,5).map(x=>x[0]);setSuggest(colors);save({...value,background:c.toDataURL('image/jpeg',.8),color:colors[0]||value.color})}catch(e:any){setError(e.message)}}
 useEffect(()=>{if(!value.background||!canvas.current)return;const img=new Image();img.onload=()=>{const c=canvas.current;if(c){c.width=img.width;c.height=img.height;c.getContext('2d')!.drawImage(img,0,0)}};img.src=value.background},[value.background]);
 const color=valid(value.color)?value.color:systemColor;
 return <Modal title={room?'此会话的背景与主题色':'应用主题色'} close={close}><p class="small muted">设置仅保存在此设备。未设置的会话使用应用主题色，应用默认跟随 Android 系统。</p>
 <section class="settings-category"><h3>主题色</h3><ColorEditor value={color} change={next=>save({...latest.current,color:next})}/><button type="button" class="text-button" disabled={!value.color} onClick={()=>save({...value,color:undefined})}><RotateCcw size={14}/>恢复默认主题色</button></section>
 {room&&<section class="settings-category"><h3>聊天背景</h3><div class="actions"><label class="button file-button"><ImagePlus size={17}/>{value.background?'更换图片':'选择图片'}<input class="package-file-input" type="file" accept="image/*" onChange={e=>{const f=e.currentTarget.files?.[0];e.currentTarget.value='';if(f)void background(f)}}/></label>{value.background&&<button type="button" onClick={()=>save({...value,background:undefined})}><Trash2 size={16}/>移除背景</button>}</div>
 {suggest.length>0&&<div class="color-presets" role="group" aria-label="图片中的颜色">{suggest.map(c=><button type="button" class={'color-swatch'+(c===color?' selected':'')} aria-label={'建议主题色 '+c} style={{background:c}} onClick={()=>save({...value,color:c})}/>)}</div>}
 {value.background&&<><p class="small muted">点击图片任意位置取色</p><canvas class="background-picker" ref={canvas} onClick={e=>{const c=e.currentTarget,r=c.getBoundingClientRect(),pixel=c.getContext('2d')!.getImageData(Math.min(c.width-1,Math.floor((e.clientX-r.left)/r.width*c.width)),Math.min(c.height-1,Math.floor((e.clientY-r.top)/r.height*c.height)),1,1).data;save({...value,color:'#'+Array.from(pixel.slice(0,3)).map(v=>v.toString(16).padStart(2,'0')).join('')})}}/></>}</section>}
 {error&&<ErrorText>{error}</ErrorText>}</Modal>
}
