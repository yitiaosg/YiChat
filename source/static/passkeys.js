(()=>{'use strict';if(window.yitiaoPasskeysLoaded)return;window.yitiaoPasskeysLoaded=true;
const supported=()=>window.isSecureContext&&!!window.PublicKeyCredential&&!!navigator.credentials;
const bytes=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-s.length%4)%4)),x=>x.charCodeAt(0));
const b64=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=/g,'');
function options(raw){const p={...raw,challenge:bytes(raw.challenge)};if(p.user)p.user={...p.user,id:bytes(p.user.id)};for(const name of ['allowCredentials','excludeCredentials'])if(p[name])p[name]=p[name].map(c=>({...c,id:bytes(c.id)}));return p;}
function serialized(c){const r={clientDataJSON:b64(c.response.clientDataJSON)};for(const k of ['attestationObject','authenticatorData','signature','userHandle'])if(c.response[k])r[k]=b64(c.response[k]);if(c.response.getTransports)r.transports=c.response.getTransports();return{id:c.id,rawId:b64(c.rawId),type:c.type,response:r,clientExtensionResults:c.getClientExtensionResults(),authenticatorAttachment:c.authenticatorAttachment};}
async function api(url,csrf,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Passkey-CSRF':csrf},body:JSON.stringify(data)});let d;try{d=await r.json();}catch{throw Error('页面已失效，请刷新后重试');}if(!r.ok)throw Error(d.error||'验证未完成，请重试');return d;}
const explain=e=>['NotAllowedError','AbortError'].includes(e.name)?'已取消或没有可用通行密钥，可重试或使用其他验证方式。':e.name==='InvalidStateError'?'这个设备上的通行密钥已经添加。':e.message||'通行密钥验证未完成';
const login=document.querySelector('[data-passkey-login]'),verify=document.querySelector('[data-passkey-verify]');
if(login||verify){const button=login||verify,status=document.getElementById('passkey-status');let controller;
 async function authenticate(conditional=false){if(!supported()){status.textContent='当前浏览器不支持通行密钥，请使用其他登录或验证方式。';return;}controller?.abort();const current=new AbortController();controller=current;const csrf=button.dataset.csrf;const kind=login?'login':'verify',query=verify&&button.dataset.flow?'?flow='+encodeURIComponent(button.dataset.flow):'';
 try{if(!conditional){button.disabled=true;status.textContent='请在设备上完成验证…';}const data=await api('/auth/passkeys/'+kind+'/options'+query,csrf,{next:button.dataset.next,remember:!!document.querySelector('[name=remember]:checked')});if(current.signal.aborted)return;
 const credential=await navigator.credentials.get({publicKey:options(data.publicKey),signal:current.signal,...(conditional?{mediation:'conditional'}:{})});if(!credential)throw Error('未选择通行密钥');if(current.signal.aborted||current!==controller)return;const result=await api('/auth/passkeys/'+kind+'/finish'+query,csrf,{token:data.token,credential:serialized(credential),context:data.context});location.assign(result.next);
 }catch(e){if(current===controller&&(!conditional||e.name!=='AbortError'))status.textContent=explain(e);}finally{if(current===controller)button.disabled=false;}}
 button.onclick=()=>authenticate();
 if(verify)authenticate();
 else if(supported()&&PublicKeyCredential.isConditionalMediationAvailable)PublicKeyCredential.isConditionalMediationAvailable().then(ok=>{if(ok&&!controller)authenticate(true);}).catch(()=>{});
}
const manager=document.querySelector('[data-passkey-manager]');
if(manager){const status=manager.querySelector('[data-passkey-status]'),csrf=manager.dataset.csrf,prefix=manager.dataset.api;
 document.querySelectorAll('[data-passkey-time]').forEach(t=>t.textContent=new Date(Number(t.dataset.passkeyTime)*1000).toLocaleString());
 const pin=()=>manager.querySelector('[name=management_pin]')?.value||'';
 const add=manager.querySelector('[data-passkey-add]');if(add)add.onclick=async()=>{if(!supported()){status.textContent='当前浏览器不支持通行密钥，请使用支持的浏览器。';return;}add.disabled=true;try{const opts=await api(prefix+'/register/options',csrf,{pin:pin()});const credential=await navigator.credentials.create({publicKey:options(opts.publicKey)});if(!credential)throw Error('未创建通行密钥，请重试');const result=await api(prefix+'/register/finish',csrf,{token:opts.token,credential:serialized(credential),name:manager.querySelector('[name=passkey_name]').value});location.assign(result.next);}catch(e){status.textContent=explain(e);add.disabled=false;}};
 manager.querySelectorAll('[data-passkey-action]').forEach(button=>button.onclick=async()=>{const item=button.closest('[data-id]'),action=button.dataset.passkeyAction;if(action==='delete'&&!confirm('移除后，这个通行密钥将无法用于此网站的验证。确定移除？'))return;button.disabled=true;try{const result=await api(prefix+'/manage',csrf,{id:item.dataset.id,action,name:item.querySelector('[data-passkey-name]').value,pin:pin()});location.assign(result.next);}catch(e){status.textContent=explain(e);button.disabled=false;}});
}
for(const block of document.querySelectorAll('[data-wallet-passkey]')){const form=block.closest('form'),label=block.querySelector('[data-pin-label]'),pin=label.querySelector('input'),retry=block.querySelector('[data-passkey-retry]'),status=block.querySelector('[data-passkey-status]');let proof=null,snapshot=null,busy=false;
 const fields=submitter=>{const d=new FormData(form);if(submitter?.name)d.append(submitter.name,submitter.value);return [...d.entries()].filter(([k])=>!['csrf','pin','passkey_response'].includes(k));};
 const fallback=e=>{proof=null;snapshot=null;label.hidden=false;pin.disabled=false;status.textContent=explain(e)+' 也可输入支付密码。';};
 async function authenticate(submitter){if(busy)return false;if(!supported()){fallback(Error('当前浏览器不支持通行密钥。'));return false;}busy=true;retry.disabled=true;try{const current=fields(submitter);status.textContent='请使用通行密钥验证…';const opts=await api('/passkeys/auth/options',block.dataset.csrf,{path:location.pathname,fields:current});const credential=await navigator.credentials.get({publicKey:options(opts.publicKey)});proof={token:opts.token,credential:serialized(credential)};snapshot=JSON.stringify(current);label.hidden=true;pin.disabled=true;status.textContent='已完成设备验证，请确认操作。';return true;}catch(e){fallback(e);return false;}finally{busy=false;retry.disabled=false;}}
 label.hidden=true;pin.disabled=true;
 retry.onclick=()=>authenticate(form.querySelector('button[type=submit],button:not([type])'));
 form.addEventListener('submit',async e=>{if(e.defaultPrevented)return;if(e.submitter?.value==='cancel')return;if(!pin.disabled&&pin.value){form.querySelector('[name=passkey_response]')?.remove();return;}if(proof&&snapshot===JSON.stringify(fields(e.submitter))){let input=form.querySelector('[name=passkey_response]');if(!input){input=document.createElement('input');input.type='hidden';input.name='passkey_response';form.append(input);}input.value=JSON.stringify(proof);return;}e.preventDefault();const submitter=e.submitter;if(await authenticate(submitter))form.requestSubmit(submitter);});
 if(block.dataset.auto==='1')authenticate(form.querySelector('button[type=submit],button:not([type])'));
}
})();
