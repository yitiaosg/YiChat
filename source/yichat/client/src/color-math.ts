/** Colour conversions for the theme editor. Hue 0–360, saturation and value 0–1. */
export type Hsv={h:number;s:number;v:number};
export const validHex=(value:unknown):value is string=>typeof value==='string'&&/^#[0-9a-f]{6}$/i.test(value);
export function hexToHsv(hex:string):Hsv{
 const [r,g,b]=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255),max=Math.max(r,g,b),delta=max-Math.min(r,g,b);
 let h=0;
 if(delta){if(max===r)h=((g-b)/delta)%6;else if(max===g)h=(b-r)/delta+2;else h=(r-g)/delta+4;h=(h*60+360)%360}
 return {h,s:max?delta/max:0,v:max};
}
export function hsvToHex({h,s,v}:Hsv){
 const f=(n:number)=>{const k=(n+h/60)%6;return v-v*s*Math.max(0,Math.min(k,4-k,1))};
 return '#'+[f(5),f(3),f(1)].map(x=>Math.round(Math.max(0,Math.min(1,x))*255).toString(16).padStart(2,'0')).join('');
}
/** Accepts “1479b8”, “#1479B8” and the short “#18b” form while typing. */
export function parseHex(text:string){
 const raw=text.trim().replace(/^#/,'').toLowerCase();
 if(/^[0-9a-f]{6}$/.test(raw))return '#'+raw;
 if(/^[0-9a-f]{3}$/.test(raw))return '#'+[...raw].map(c=>c+c).join('');
 return undefined;
}
