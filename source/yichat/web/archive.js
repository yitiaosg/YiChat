'use strict';
let trusted, downloadUrl;
const node=id=>document.getElementById(id);
node('archive-form').addEventListener('submit',async event=>{
  event.preventDefault();trusted=undefined;node('download').hidden=true;node('summary').textContent='';
  const base='/api/archives/'+encodeURIComponent(node('room').value)+'/'+node('day').value;
  const query='?spec='+encodeURIComponent(node('spec').value);
  try{
    const r=await fetch(base+'/summary'+query,{cache:'no-store'}),data=await r.json();
    if(!r.ok)throw new Error(data.error||'请求失败');
    trusted=data;node('summary').textContent=JSON.stringify(data,null,2);node('status').textContent='已从认证服务器取得可信摘要';
    downloadUrl=base+'/download'+query;node('download').href=downloadUrl;node('download').hidden=Boolean(data.cleaned_at);
    if(data.cleaned_at)node('status').textContent+='；实体已清理，仍可验证本地文件';
  }catch(e){node('status').textContent=e.message;}
});
node('local-file').addEventListener('change',async event=>{
  const file=event.target.files[0];if(!file)return;
  if(!trusted){node('verification').textContent='请先取得对应日期及规格的可信摘要';return;}
  // Web Crypto hashes locally. No file, private text or key is sent to the server.
  const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
  const actual=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
  node('verification').textContent=actual===trusted.sha256 && file.size===trusted.bytes ? '校验通过：与服务器存证逐字节一致' : '校验失败：文件、日期或规格不匹配';
});
