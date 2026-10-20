'use strict';
const id=location.pathname.split('/').pop(),node=id=>document.getElementById(id);
let order;
async function request(action){
  try{
    const r=await fetch('/api/orders/'+id+(action?'/'+action:''),action?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(action==='fund'?{}:{room:order.room})}:{cache:'no-store'});
    const data=await r.json();if(!r.ok)throw new Error(data.error||'请求失败');
    order=data;node('order').textContent=JSON.stringify(data,null,2);node('status').textContent='状态：'+data.state;
    node('authorize').hidden=!data.authorize_url;if(data.authorize_url)node('authorize').href=data.authorize_url;
    node('fund').hidden=!data.actions.fund;node('receive').hidden=!data.actions.receive;node('claim').hidden=!data.actions.claim;
  }catch(e){node('status').textContent=e.message;}
}
for(const action of ['fund','receive','claim'])node(action).onclick=()=>request(action);
request();
