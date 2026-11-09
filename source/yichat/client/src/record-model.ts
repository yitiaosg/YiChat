export type IdentityDisplay='avatar'|'name'|'both'|'none';
export function recordHash(content:any):string|undefined{
 const hash=content?.['org.yitiao.record'];if(typeof hash==='string'&&/^[a-f0-9]{64}$/.test(hash))return hash;
 return String(content?.body||'').match(/https:\/\/chat\.yitiao\.org\/records\/([a-f0-9]{64})(?=$|[\s，。！？、；：）】》」』.,!;:)])/u)?.[1];
}
export function individualAllowed(content:any):boolean{
 const share=content?.['org.yitiao.share']||{},business=content?.['org.yitiao.business']||{};
 if(['order','receipt','gift'].includes(share.type)||['order','receipt','gift'].includes(business.kind)||/https:\/\/chat\.yitiao\.org\/(orders|gifts)\/[A-Za-z0-9_-]{32}/.test(String(content?.body||'')+' '+String(share.url||'')))return false;
 const previous=content?.['org.yitiao.forward_snapshot'];return previous?.content?individualAllowed(previous.content):true;
}
export const recordUrl=(hash:string)=>'https://chat.yitiao.org/records/'+hash;
export async function verifyRecord(hash:string,raw:string){
 const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)))).map(n=>n.toString(16).padStart(2,'0')).join('');
 if(digest!==hash)throw Error('记录哈希校验不一致');return JSON.parse(raw);
}

// Legacy records may contain the URL once in the body and once in share.url.
// Deduplicate only the presentation; retain signed canonical bytes unchanged.
export function distinctPreviews(previews:any[]=[]){
 const seen=new Set<string>();return previews.filter(p=>{const key=String(p.type||'')+'|'+String(p.url||'')+'|'+String(p.data?.id||'')+'|'+String(!!p.receipt);if(seen.has(key))return false;seen.add(key);return true});
}
