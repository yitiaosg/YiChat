/* Large design serialisation/compression stays off the UI thread. */
self.onmessage=async ({data})=>{
 try{
  self.postMessage({phase:'正在打包图片和设计…'});
  const raw=new TextEncoder().encode(JSON.stringify(data));
  if(raw.byteLength>32*1024*1024)throw Error('设计资源总大小超过 32 MB');
  let bytes=raw,encoding=null;
  if(typeof CompressionStream!=='undefined'&&raw.byteLength>32768){
   self.postMessage({phase:'正在压缩上传数据…'});
   bytes=new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer());encoding='gzip';
  }
  self.postMessage({bytes:bytes.buffer,encoding,rawBytes:raw.byteLength},[bytes.buffer]);
 }catch(e){self.postMessage({error:e.message})}
};
