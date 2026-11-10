import {useEffect,useRef,useState} from 'preact/hooks';
import {Check} from 'lucide-preact';
import {hexToHsv,hsvToHex,parseHex,type Hsv} from './color-math';
const PRESETS=['#1479b8','#2f6fed','#6c5ce7','#a855c7','#d6457e','#d9483b','#e07b24','#c79a12','#4f9d3a','#0f9f8f','#3d8fa8','#5d6b7a'];
const clamp=(n:number)=>Math.max(0,Math.min(1,n));
/** In-page colour editor: saturation/brightness area, hue strip, presets and a hex field. */
export function ColorEditor({value,change}:{value:string;change:(color:string)=>void}){
 const [hsv,setHsv]=useState<Hsv>(()=>hexToHsv(value)),[text,setText]=useState(value.toUpperCase());
 const latest=useRef(change);latest.current=change;
 const sent=useRef(value.toLowerCase()),timer=useRef<ReturnType<typeof setTimeout>>(),queued=useRef<string>();
 // Outside changes (presets from a background, reset) move the handles; our own echoes keep the hue of greys.
 useEffect(()=>{if(value.toLowerCase()!==sent.current){sent.current=value.toLowerCase();setHsv(hexToHsv(value));setText(value.toUpperCase())}},[value]);
 useEffect(()=>()=>{clearTimeout(timer.current);if(queued.current)latest.current(queued.current)},[]);
 function emit(color:string,now=false){
  sent.current=color;queued.current=color;
  const flush=()=>{timer.current=undefined;const next=queued.current;queued.current=undefined;if(next)latest.current(next)};
  if(now){clearTimeout(timer.current);flush()}else if(!timer.current)timer.current=setTimeout(flush,48);
 }
 function update(next:Hsv,now=false){setHsv(next);const color=hsvToHex(next);setText(color.toUpperCase());emit(color,now)}
 function track(point:(x:number,y:number)=>Hsv){
  const move=(e:PointerEvent,now=false)=>{const box=(e.currentTarget as HTMLElement).getBoundingClientRect();update(point(clamp((e.clientX-box.left)/box.width),clamp((e.clientY-box.top)/box.height)),now)};
  return {
   onPointerDown(e:PointerEvent){if(e.button)return;e.preventDefault();const node=e.currentTarget as HTMLElement;try{node.setPointerCapture(e.pointerId)}catch{/* Pointer already released. */}node.dataset.dragging='1';node.focus({preventScroll:true});move(e)},
   onPointerMove(e:PointerEvent){if((e.currentTarget as HTMLElement).dataset.dragging)move(e)},
   onPointerUp(e:PointerEvent){const node=e.currentTarget as HTMLElement;if(node.dataset.dragging){delete node.dataset.dragging;move(e,true)}},
   onPointerCancel(e:PointerEvent){delete (e.currentTarget as HTMLElement).dataset.dragging}
  };
 }
 const hue=hsv.h,pure=hsvToHex({h:hue,s:1,v:1}),color=hsvToHex(hsv);
 function areaKey(e:KeyboardEvent){const step=e.shiftKey?.1:.02,d:Record<string,[number,number]>={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,step],ArrowDown:[0,-step]},m=d[e.key];if(!m)return;e.preventDefault();update({h:hue,s:clamp(hsv.s+m[0]),v:clamp(hsv.v+m[1])},true)}
 function hueKey(e:KeyboardEvent){const step=e.shiftKey?20:4,d:Record<string,number>={ArrowLeft:-step,ArrowDown:-step,ArrowRight:step,ArrowUp:step},m=d[e.key];if(!m)return;e.preventDefault();update({...hsv,h:Math.max(0,Math.min(359.9,hue+m))},true)}
 return <div class="color-editor">
  <div class="color-area" role="slider" tabIndex={0} aria-label="饱和度与亮度" aria-valuetext={'饱和度 '+Math.round(hsv.s*100)+'%，亮度 '+Math.round(hsv.v*100)+'%'} style={{background:`linear-gradient(to top,#000,transparent),linear-gradient(to right,#fff,${pure})`}} onKeyDown={areaKey} {...track((x,y)=>({h:hue,s:x,v:1-y}))}><i class="color-thumb" style={{left:hsv.s*100+'%',top:(1-hsv.v)*100+'%',background:color}}/></div>
  <div class="color-hue" role="slider" tabIndex={0} aria-label="色相" aria-valuemin={0} aria-valuemax={360} aria-valuenow={Math.round(hue)} onKeyDown={hueKey} {...track(x=>({...hsv,h:Math.min(359.9,x*360)}))}><i class="color-thumb" style={{left:hue/360*100+'%',background:pure}}/></div>
  <div class="color-presets" role="group" aria-label="预设颜色">{PRESETS.map(preset=><button type="button" key={preset} class={'color-swatch'+(preset===color?' selected':'')} aria-label={'预设颜色 '+preset} aria-pressed={preset===color} style={{background:preset}} onClick={()=>update(hexToHsv(preset),true)}>{preset===color&&<Check size={15}/>}</button>)}</div>
  <div class="color-value"><span class="color-chip" style={{background:color}}/><label>十六进制色值<input type="text" inputMode="text" autoComplete="off" autoCapitalize="characters" spellcheck={false} maxLength={7} aria-invalid={!parseHex(text)} value={text} onInput={e=>{const raw=e.currentTarget.value;setText(raw);const parsed=parseHex(raw);if(parsed&&raw.replace(/^#/,'').length===6){setHsv(hexToHsv(parsed));emit(parsed,true)}}} onBlur={()=>{const parsed=parseHex(text);if(parsed){setHsv(hexToHsv(parsed));emit(parsed,true);setText(parsed.toUpperCase())}else setText(color.toUpperCase())}}/></label></div>
 </div>;
}
