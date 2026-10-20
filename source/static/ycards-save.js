window.saveYCardsDesign=async function({path,payload,csrf,onProgress}){
 const packed=await new Promise((resolve,reject)=>{
  const worker=new Worker('/static/ycards-save-worker.js');
  worker.onmessage=({data})=>{if(data.phase){onProgress(data.phase);return}worker.terminate();data.error?reject(Error(data.error)):resolve(data)};
  worker.onerror=()=>{worker.terminate();reject(Error('保存准备失败，请刷新后重试'))};
  worker.postMessage(payload);
 });
 onProgress(`正在上传 ${(packed.bytes.byteLength/1048576).toFixed(2)} MB · 0%`);
 return await new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();xhr.open('POST','/ycards/api/'+path+'?compact=1');xhr.timeout=180000;
  xhr.setRequestHeader('Content-Type','application/json');xhr.setRequestHeader('X-YCards-CSRF',csrf);if(packed.encoding)xhr.setRequestHeader('Content-Encoding',packed.encoding);
  xhr.upload.onprogress=e=>{onProgress(e.lengthComputable?`正在上传 · ${Math.floor(e.loaded/e.total*100)}%`:'正在上传…')};
  xhr.upload.onload=()=>onProgress('上传完成，正在校验并保存…');
  xhr.onload=()=>{let value;try{value=JSON.parse(xhr.responseText)}catch{reject(Error('服务器响应异常，请重试保存'));return}if(xhr.status>=200&&xhr.status<300)resolve(value);else{if(value.redirect)location.href=value.redirect;reject(Error(value.error||'保存失败'))}};
  xhr.onerror=()=>reject(Error('连接中断，将检查保存结果'));xhr.ontimeout=()=>reject(Error('等待超时，将检查保存结果'));xhr.send(packed.bytes);
 });
};
