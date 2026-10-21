'use strict';
document.addEventListener('DOMContentLoaded', () => {
  const csrf = document.querySelector('meta[name="social-csrf"]')?.content || document.querySelector('input[name="csrf"]')?.value;
  const status = document.getElementById('social-status');
  function show(text) { if (status) status.textContent = text; }
  async function call(url, data) {
    const r = await fetch(url, {method: data ? 'POST' : 'GET', headers: data ? {'Content-Type':'application/json','X-CSRF-Token':csrf} : {}, body:data ? JSON.stringify(data) : undefined});
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || '请求失败，请重试');
    return body;
  }
  const relation = document.querySelector('[data-social-follow]');
  function render(body) {
    if (relation) {
      relation.dataset.following = String(body.following);
      (relation.querySelector('[data-follow-label]')||relation).textContent = body.mutual ? '互相关注' : body.following ? '已关注' : '关注';
    }
    for (const key of ['following','followers','friends']) {
      const node = document.querySelector(`[data-count="${key}"]`);
      if (node) { const n = body.counts[key]; node.textContent = n>=1e6 ? `${+(n/1e6).toFixed(1)}m` : n>=1e3 ? `${+(n/1e3).toFixed(1)}k` : String(n); node.title=String(n); }
    }
    const message=document.querySelector('[data-social-message]');
    if (message) message.hidden=!body.can_message;
  }
  relation?.addEventListener('click', async () => {
    relation.disabled=true;
    try { render(await call(`/api/social/${relation.dataset.socialFollow}/${relation.dataset.following==='true' ? 'unfollow' : 'follow'}`,{})); show('已保存'); }
    catch(e) { show(e.message); }
    finally { relation.disabled=false; }
  });
  document.querySelector('[data-social-message]')?.addEventListener('click',event=>{location.href='https://chat.yitiao.org/?to='+encodeURIComponent(event.currentTarget.dataset.socialMessage);});
  for (const node of document.querySelectorAll('[data-social-list]')) {
    const button=document.createElement('button');button.textContent='加载名单';node.append(button);
    let cursor='',done=false;
    async function load() {
      if(done || button.disabled)return;button.disabled=true;
      try {
        const data=await call(`/api/social/${node.dataset.socialId}/lists/${node.dataset.socialList}?cursor=${encodeURIComponent(cursor)}`);
        for(const item of data.items){
          const row=document.createElement('p'),a=document.createElement('a');a.href='/@'+encodeURIComponent(item.username)+'/';a.textContent=(item.nickname||item.username)+' @'+item.username;row.append(a);
          if(node.dataset.socialList==='blocked') { const b=document.createElement('button');b.textContent='解除拉黑';b.onclick=async()=>{try{await call(`/api/social/${item.uid}/unblock`,{});row.remove();show('已解除拉黑');}catch(e){show(e.message);}};row.append(b); }
          node.insertBefore(row,button);
        }
        cursor=data.next_cursor;done=!cursor;button.textContent=done ? (data.items.length ? '已加载全部' : '名单为空') : '加载更多';
      }catch(e){show(e.message);}finally{button.disabled=done;}
    }
    button.onclick=load;
    // A visible sentinel lazily fetches the next page; permissions remain server-side.
    new IntersectionObserver(entries=>{if(entries[0].isIntersecting)load();}).observe(button);
  }
  document.getElementById('block-form')?.addEventListener('submit',async e=>{
    e.preventDefault();try{await call('/api/social/block-username',{username:new FormData(e.target).get('username')});location.reload();}catch(err){show(err.message);}
  });
});
