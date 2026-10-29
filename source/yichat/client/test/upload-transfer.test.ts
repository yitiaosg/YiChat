import test from 'node:test';import assert from 'node:assert/strict';
import {transferFile,type TransferReply,type TransferCall} from '../src/upload-transfer';
const uid='a'.repeat(48);
function fixture(size:number,fault:'none'|'start-ack'|'chunk-ack'|'before-chunk'|'finish-ack'='none'){
 let offset=0,complete=false,created=false,lost=false;const pieces:Uint8Array[]=[],puts:number[]=[],keys:string[]=[],progress:number[]=[];
 const reply=():TransferReply=>({id:uid,state:complete?'complete':'uploading',offset,bytes:size,chunk_bytes:65536,...(complete?{url:'mxc://chat.yitiao.org/'+uid,expires:123456789}: {})});
 const call:TransferCall=async(path,method,body)=>{
  if(path==='/api/uploads'){keys.push((body as any).key);created=true;if(fault==='start-ack'&&!lost){lost=true;throw TypeError('synthetic lost start response')}return reply()}
  if(method==='GET')return reply();
  if(method==='PUT'){
   puts.push(offset);assert.ok((body as Blob).size<=65536);assert.ok((progress.at(-1)||0)<=offset);
   if(fault==='before-chunk'&&!lost){lost=true;throw TypeError('synthetic network reset')}
   const data=new Uint8Array(await (body as Blob).arrayBuffer());pieces.push(data);offset+=data.length;
   if(fault==='chunk-ack'&&!lost){lost=true;throw TypeError('synthetic lost chunk response')}
   return reply();
  }
  if(path.endsWith('/complete')){assert.equal(offset,size);complete=true;if(fault==='finish-ack'&&!lost){lost=true;throw TypeError('synthetic lost final response')}return reply()}
  throw Error('unexpected request');
 };
 return {call,progress,puts,keys,pieces,created:()=>created};
}
for(const fault of ['none','start-ack','chunk-ack','before-chunk','finish-ack'] as const)test('acknowledged upload recovers '+fault,async()=>{
 const bytes=new Uint8Array(300000).fill(42),file=new Blob([bytes]),f=fixture(file.size,fault),state={key:'stable-upload-request'};
 file.arrayBuffer=async()=>{throw Error('whole-file buffering is forbidden')};
 const result=await transferFile(file,'!room',state,f.call,new AbortController().signal,(n)=>f.progress.push(n),async()=>{});
 assert.equal(result.state,'complete');assert.equal(f.progress.at(-1),bytes.length);assert.equal(new Set(f.keys).size,1);
 assert.equal(f.pieces.reduce((n,p)=>n+p.length,0),bytes.length);assert.ok(f.pieces.every(p=>p.every(b=>b===42)));
});
test('forbidden upload fails without retrying or reporting completion',async()=>{
 let calls=0;const progress:number[]=[];
 await assert.rejects(transferFile(new Blob(['data']),'!room',{key:'new-upload'},async()=>{calls++;throw Object.assign(Error('forbidden'),{status:403})},new AbortController().signal,n=>progress.push(n),async()=>{}),/forbidden/);
 assert.equal(calls,1);assert.deepEqual(progress,[]);
});
test('manual retry resumes acknowledged bytes after network retry budget is exhausted',async()=>{
 const file=new Blob([new Uint8Array(200000).fill(7)]),f=fixture(file.size),state:{key:string;id?:string}={key:'retry-in-current-page'};
 let offline=false;const sent:number[]=[];
 const call:TransferCall=async(path,method,body,headers)=>{
  if(offline)throw TypeError('synthetic offline');
  if(method==='PUT'){sent.push(Number(headers?.['X-Upload-Offset']));if(sent.length===2){offline=true;throw TypeError('synthetic offline')}}
  return f.call(path,method,body,headers);
 };
 await assert.rejects(transferFile(file,'!room',state,call,new AbortController().signal,()=>{},async()=>{}),/offline/);
 assert.equal(state.id,uid);offline=false;
 const result=await transferFile(file,'!room',state,call,new AbortController().signal,()=>{},async()=>{});
 assert.equal(result.state,'complete');assert.deepEqual(sent,[0,65536,65536,131072,196608]);
 assert.equal(f.pieces.reduce((n,p)=>n+p.length,0),file.size);assert.equal(f.keys.length,1);
});
