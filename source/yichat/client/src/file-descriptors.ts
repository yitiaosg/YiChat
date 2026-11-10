import type {PacketFile} from './packet-protocol';import {sha256} from './packet-protocol';
const saved=new Map<string,PacketFile>();
export function rememberFiles(rows:PacketFile[]=[]){for(const row of rows)if(/^[a-f0-9]{48}$/.test(row.id)&&/^[a-f0-9]{64}$/.test(row.sha256))saved.set(row.id,{id:row.id,bytes:String(row.bytes),sha256:row.sha256})}
export async function checkFile(id:string,blob:Blob){const row=saved.get(id);if(row&&(blob.size!==Number(row.bytes)||await sha256(new Uint8Array(await blob.arrayBuffer()))!==row.sha256))throw Error('附件内容与保存的摘要不符');return blob}
if(typeof window!=='undefined')window.addEventListener('yc:locked',()=>saved.clear());
