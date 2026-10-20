/* Auction UI shares YCards identity, dialogs and renderer. */
window.createYCardsAuctions = function (ctx) {
  'use strict';
  const {api, assets, boot, main, modal, modalBody, dialog, closeModal, esc, money, toast, route, refreshState, guard, addModalRenderer, shareCard, renameCard, person} = ctx;
  const $ = (s, root = document) => root.querySelector(s);
  const states = {active: '竞拍中', sold: '已成交', unsold: '流拍', cancelled: '已取消并退款'};
  let version = 0, observer, renderers = [], current = null, requestScope=null, previewScope=null, detailBusy = false;
  const formatDate = n => new Date(n * 1000).toLocaleString();
  const rule = a => `${a.kind === 'sealed' ? '密封竞价' : '公开拍卖'} · ${a.pricing === 'second' ? '第二价格' : '第一价格'}`;
  const remaining = (end, now) => {
    let s = Math.max(0, Math.ceil(end - now));
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
    return s ? `${h}时 ${m}分 ${s % 60}秒` : '正在结算';
  };
  function stop() {
    requestScope?.abort();previewScope?.abort();requestScope=null;
    version++; observer?.disconnect(); renderers.forEach(r => r.destroy()); renderers = []; current = null;
  }
  function priceText(a) {
    if (a.state === 'sold') return a.price_cents === null ? '成交价格保密' : `${money(a.price_cents)} YIDT`;
    return a.current_cents === null ? '报价保密' : `${money(a.current_cents)} YIDT`;
  }
  function tileBody(a, now) {
    return `<h3>${esc(a.name)}</h3><div class="cid">${esc(a.cid)}</div>
      <div class="tags"><span class="tag">${rule(a)}</span><span class="tag">${states[a.state]}</span>${a.won ? '<span class="tag">竞拍成功</span>' : ''}</div>
      <div class="price-tag">${priceText(a)}</div><p class="helper">起拍 ${money(a.start_cents)} YIDT · ${a.bid_count} 人出价<br>
      ${a.state === 'active' ? `剩余 ${remaining(a.ends, now)}<br>截止 ` : '截止于 '}${formatDate(a.ends)}</p>
      ${a.my_bid_cents !== null ? `<p class="helper">我的报价 ${money(a.my_bid_cents)} YIDT${a.leading ? ' · 当前领先' : ''}${a.held_cents ? `<br>冻结 ${money(a.held_cents)} YIDT` : ''}</p>` : ''}
      ${person(a.seller_profile,'卖家')}
      <button data-auction-open="${a.id}">查看拍品</button>`;
  }
  async function page(history = false) {
    stop(); const v = version;
    main.innerHTML = `<div class="page-title"><div><p class="eyebrow">${history ? 'AUCTION HISTORY' : 'AUCTIONS'}</p><h1>${history ? '拍卖历史' : '竞拍心动之作'}</h1><p class="muted">${history ? '查看我的拍品和参与过的竞拍。' : '每一张闪卡，等待下一位收藏者。'}</p></div></div>
      ${history ? '<div class="history-tabs" id="auction-sections"><button data-section="active" class="active">正在进行</button><button data-section="sold">已经成交</button><button data-section="participated">参与竞拍</button><button data-section="ended">流拍／取消</button></div>' : ''}
      <form class="filters" id="auction-filter"><input name="tid" placeholder="TID" maxlength="12" aria-label="TID"><input name="name" placeholder="拍品名称" aria-label="拍品名称"><button>筛选</button></form>
      <div class="grid" id="auction-grid"></div><div class="row pager" id="auction-pages"></div>`;
    current = {history, page: 1, section: 'active', query: '', ids: '', v};
    $('#auction-filter').onsubmit = e => {e.preventDefault(); current.query = new URLSearchParams(new FormData(e.target)).toString(); current.page = 1; load(true).catch(e => toast(e.message));};
    if (history) $('#auction-sections').onclick = e => {
      if (!e.target.dataset.section) return;
      current.section = e.target.dataset.section; current.page = 1;
      [...e.target.parentNode.children].forEach(b => b.classList.toggle('active', b === e.target));
      load(true).catch(e => toast(e.message));
    };
    $('#auction-pages').onclick=e=>{const b=e.target.closest('[data-page]');if(b){current.page+=Number(b.dataset.page);load(true).catch(e=>toast(e.message))}};
    $('#auction-pages').onsubmit=e=>{e.preventDefault();if(!e.target.reportValidity())return;current.page=Number(new FormData(e.target).get('page'));load(true).catch(e=>toast(e.message))};
    $('#auction-grid').onclick = e => {const b = e.target.closest('[data-auction-open]'); if (b) detail(b.dataset.auctionOpen).catch(e => toast(e.message));};
    await load();
  }
  async function load(replace = false) {
    if (!current || (requestScope && !replace)) return;
    const c = current, v = version;
    if(replace){requestScope?.abort();previewScope?.abort();observer?.disconnect();renderers.forEach(r=>r.destroy());renderers=[];c.ids='';$('#auction-grid').innerHTML='<div class="empty">正在加载…</div>'}
    const scope=requestScope=new AbortController();
    try {
      const query = `${c.query}&page=${c.page}&section=${c.section}&compact=1`;
      const result = await api(`${c.history ? 'auction-history' : 'auctions'}?${query}`,undefined,{signal:scope.signal});
      if (scope.signal.aborted || v !== version || c !== current) return;
      c.page=result.page;
      const signature = result.items.map(a => a.id).join(',');
      const grid = $('#auction-grid'); if (!grid) return;
      if (signature !== c.ids || !grid.children.length || grid.querySelector('.empty')) {
        previewScope?.abort();const images=previewScope=new AbortController();
        observer?.disconnect(); renderers.forEach(r => r.destroy()); renderers = [];
        grid.innerHTML = result.items.length ? result.items.map(a => `<article class="tile"><div class="preview" data-auction="${a.id}" data-instance="${a.instance}" tabindex="0" role="button" aria-label="查看 ${esc(a.name)}"></div><div class="tile-body" data-body="${a.id}"></div></article>`).join('') : '<div class="empty"><strong>暂无拍品</strong>在“我的”中选择一张卡片发起拍卖。</div>';
        const cards=new Map(result.items.map(a=>[a.id,a.card]));assets.preload([...cards.values()],images.signal);
        observer = new IntersectionObserver(entries => {
          for (const entry of entries) {
            const el = entry.target;
            if (!entry.isIntersecting || el.dataset.loaded) continue;
            el.dataset.loaded = '1';
            assets.hydrate(cards.get(el.dataset.auction),images.signal).then(card => {
              if (images.signal.aborted || v !== version || !el.isConnected) return;
              renderers.push(new YCardRenderer(el, card, {onSelect: () => detail(el.dataset.auction).catch(e => toast(e.message))}));
            }).catch(e => {if(e.name!=='AbortError'&&el.isConnected)el.textContent=e.message});
          }
        }, {rootMargin: '700px'});
        grid.querySelectorAll('.preview').forEach(el => {
          observer.observe(el); el.onkeydown = e => {if (e.key === 'Enter') detail(el.dataset.auction).catch(e => toast(e.message));};
        });
        c.ids = signature;
      }
      for (const a of result.items) $(`[data-body="${a.id}"]`, grid).innerHTML = tileBody(a, result.now);
      const pages=$('#auction-pages');if(!pages.contains(document.activeElement))pages.innerHTML=assets.pager(c.page,result.total,'件');
    } catch(error){if(error.name!=='AbortError')throw error}
    finally {if(requestScope===scope)requestScope=null;}
  }
  function table(a) {
    if (a.bids === null) return '<p class="helper">密封报价表仅拍品所有者可见。你只能查看自己的报价，无法查看其他人的报价或身份。</p>';
    if (!a.bids.length) return '<p class="helper">尚无人出价</p>';
    return `<div class="auction-bids"><table class="quote-table"><thead><tr><th>报价 YIDT</th>${a.kind === 'public' ? '<th>用户</th><th>时间</th>' : ''}</tr></thead><tbody>${a.bids.map(b => `<tr><td>${money(b.amount_cents)}</td>${a.kind === 'public' ? `<td>${esc(b.user)}</td><td>${formatDate(b.created)}</td>` : ''}</tr>`).join('')}</tbody></table></div>${a.kind === 'sealed' ? '<p class="helper">仅显示每位竞拍者的最高报价，不显示身份。</p>' : ''}`;
  }
  function updateDetail(a) {
    const host = $('#auction-detail', modalBody);
    if (!host || host.dataset.id !== a.id) return;
    const name=host.querySelector('[data-card-name]');if(name)name.textContent=a.name;
    $('#auction-info', host).innerHTML = `<div class="tags"><span class="tag">${rule(a)}</span><span class="tag">${states[a.state]}</span></div>
      <h2 class="auction-price">${priceText(a)}</h2><p class="muted">起拍 ${money(a.start_cents)} YIDT · ${a.bid_count} 人出价<br>${a.kind === 'public' ? `最小加价 ${money(a.increment_cents)} YIDT<br>` : ''}${a.state === 'active' ? `剩余 ${remaining(a.ends, a.now)}<br>` : ''}截止 ${formatDate(a.ends)}</p>
      <p class="helper">${a.clock === 'rolling' ? `有效出价后重新倒计时 ${a.rolling_seconds / 3600} 小时。` : `持续 ${a.duration_seconds / 3600} 小时${a.extension_seconds ? `；临近截止时保证剩余 ${a.extension_seconds / 3600} 小时` : ''}。`}<br>${a.pricing === 'second' ? '最高报价者按另一位竞拍者的次高报价成交，仅一人出价则按起拍价成交。' : '最高报价者按自己的报价成交。'}同价时先达到该报价者优先。</p>
      ${a.my_bid_cents !== null ? `<p>我的报价 ${money(a.my_bid_cents)} YIDT${a.leading ? ' · 当前领先' : ''}<br>冻结 ${money(a.held_cents)} YIDT</p>` : ''}
      ${a.owned && a.state==='sold'?person(a.buyer_profile,'买家'):''}
      ${a.won ? `<p class="auction-success">竞拍成功！成交 ${money(a.price_cents)} YIDT，手续费 ${money(a.fee_cents)} YIDT，卡片已入库。</p>` : ''}
      ${a.owned && a.state === 'active' ? '<p class="helper">拍卖期间卡片锁定，不能挂售或撤回拍卖。</p>' : ''}`;
    if(a.kind==='public' && a.yidt_receipts?.length)$('#auction-info',host).innerHTML+=a.yidt_receipts.map(receipt=>`<p><a target="_blank" rel="noopener" href="https://yidt.yitiao.org/explorer/tx/${receipt.hash}">${esc(receipt.label)}</a></p>`).join('');
    $('#auction-bid-table', host).innerHTML = table(a);
    const form = $('#auction-bid-form', host);
    form.hidden = a.owned || a.state !== 'active' || !boot.allowed;
    $('#auction-login', host).hidden = boot.allowed || a.state !== 'active';
    const input = $('#auction-amount', host);
    input.min = (a.minimum_cents / 100).toFixed(2);
    input.placeholder = `至少 ${money(a.minimum_cents)}`;
    $('#auction-reserve-note', host).textContent = a.kind === 'sealed' ? '报价及预估手续费将转入官方钱包托管，并支付 gas；退款到账再次扣除 gas。报价只能提高、不可撤回。' : '报价及预估手续费将转入官方钱包托管，并支付 gas；被超价后退款，退款到账扣除 gas。出价不可撤回。';
  }
  async function detail(id) {
    const a = await api('auctions/' + id+'?compact=1');
    a.card=await assets.hydrate(a.card);
    dialog(`<div id="auction-detail" data-id="${a.id}"><div class="detail-grid"><div><div class="detail-preview interactive-drag"></div><h2 data-card-name>${esc(a.name)}</h2><p class="cid">${esc(a.cid)}</p><p class="helper">拖动旋转查看拍品</p>${person(a.seller_profile,'卖家')}${a.card.owned?'<button id="rename-auction-card">修改名称</button>':''}<button id="share-auction-card" ${a.card.destroyed||(!a.card.owned&&a.state!=='active')?'disabled':''}>分享</button></div><div><div id="auction-info"></div><form id="auction-bid-form"><label class="field">我的报价 YIDT<input id="auction-amount" type="number" step="0.01" required></label><p class="helper" id="auction-reserve-note"></p><p class="helper" id="auction-reserve-total"></p><button class="primary" id="submit-bid">确认出价</button></form><button id="auction-login">登录／升级后参与竞拍</button></div></div><h3>报价表</h3><div id="auction-bid-table"></div></div>`);
    addModalRenderer(new YCardRenderer($('.detail-preview', modalBody), a.card)); updateDetail(a);
    if($('#rename-auction-card'))$('#rename-auction-card').onclick=()=>renameCard(a.card).catch(e=>toast(e.message));
    $('#share-auction-card').onclick=()=>shareCard(a.card).catch(e=>toast(e.message));
    $('#auction-login').onclick = () => guard('?tab=auction&auction=' + a.id);
    let requestKey, requestAmount;
    const input = $('#auction-amount');
    input.oninput = () => {const p = Number(input.value); $('#auction-reserve-total').textContent = Number.isFinite(p) && p > 0 ? `预计冻结 ${money(Math.round(p * 100) + Math.ceil(p * .005) * 100)} YIDT，含预估手续费 ${Math.ceil(p * .005)} YIDT。` : '';};
    $('#auction-bid-form').onsubmit = async e => {
      e.preventDefault(); if (!guard('?tab=auction&auction=' + a.id)) return;
      const amount = input.value;
      if (requestAmount !== amount) {requestAmount = amount; requestKey = crypto.randomUUID();}
      const button = $('#submit-bid'); button.disabled = true;
      try {
        await api('bid', {auction_id: a.id, amount, request_key: requestKey});
        requestKey = null; requestAmount = null; toast('报价已提交');
        await Promise.all([refreshDetail(), refreshState(), load()]);
      } catch (err) {toast(err.message); await refreshDetail().catch(() => {});}
      finally {button.disabled = false;}
    };
  }
  async function refreshDetail() {
    const host = $('#auction-detail', modalBody);
    if (!modal.open || !host || detailBusy) return;
    detailBusy = true;
    try {const a = await api('auctions/' + host.dataset.id+'?compact=1&card=0'); if (host.isConnected) updateDetail(a);}
    finally {detailBusy = false;}
  }
  function create(card) {
    const requestKey = crypto.randomUUID();
    dialog(`<h2>发起拍卖 · ${esc(card.name)}</h2><p class="cid">${esc(card.cid)}</p><form id="create-auction" class="panel">
      <div class="form-grid"><label>竞价方式<select name="kind"><option value="public">公开拍卖</option><option value="sealed">密封竞价</option></select></label><label>成交规则<select name="pricing"><option value="first">第一价格拍卖</option><option value="second">第二价格拍卖</option></select></label>
      <label>起拍价 YIDT<input name="start_price" type="number" min="0.01" step="0.01" required></label><label id="auction-increment">最小加价 YIDT<input name="increment" type="number" min="0.01" step="0.01" value="1" required></label></div>
      <label id="auction-clock">截止规则<select name="clock"><option value="fixed">持续时间＋延时</option><option value="rolling">滚动截止时间</option></select></label>
      <label id="auction-duration">持续时间（最多 336 小时）<input name="duration_hours" type="number" min="0.001" max="336" step="any" value="24" required></label>
      <label id="auction-rolling" hidden>每次有效出价后重新计时（最多 24 小时）<input name="rolling_hours" type="number" min="0.001" max="24" step="any" value="1"></label>
      <label id="auction-extension">临近截止延时（0～12 小时）<input name="extension_hours" type="number" min="0" max="12" step="any" value="0.1"></label>
      <p class="helper">每位用户最多同时拍卖 10 张卡片。成功发布收取 10 YIDT 发布手续费，下架／流拍不退。发布后规则不可修改，拍品不可同时挂售，拍卖不可撤回。无人出价则流拍并保留在仓库。买家另付成交价 5‰、向上取整至整 YIDT 的手续费。</p>
      <button class="primary" id="publish-auction">发布拍卖</button></form>`);
    const form = $('#create-auction');
    function fields() {
      const sealed = form.elements.kind.value === 'sealed';
      if (sealed) form.elements.clock.value = 'fixed';
      const rolling = !sealed && form.elements.clock.value === 'rolling';
      for (const [id, name, show] of [['auction-increment','increment',!sealed],['auction-clock','clock',!sealed],['auction-duration','duration_hours',!rolling],['auction-rolling','rolling_hours',rolling],['auction-extension','extension_hours',!sealed && !rolling]]) {
        $('#' + id).hidden = !show; form.elements[name].disabled = !show;
      }
      form.elements.increment.max = form.elements.start_price.value || '';
    }
    form.oninput = fields; fields();
    form.onsubmit = async e => {
      e.preventDefault(); if (!form.reportValidity()) return;
      if (!confirm('确认支付 10 YIDT 发布手续费？发布后不可修改规则或撤回拍卖，流拍不退发布费。')) return;
      const button = $('#publish-auction'); button.disabled = true;
      const data = Object.fromEntries(new FormData(form));
      try {
        const created = await api('auctions', {...data, clock: data.clock || 'fixed', instance_id: card.id, request_key: requestKey});
        closeModal(); toast('拍卖已发布，发布手续费 10 YIDT'); await refreshState(); await route('auction'); await detail(created.id);
      } catch (err) {toast(err.message); button.disabled = false;}
    };
  }
  setInterval(() => {
    if (document.hidden) return;
    if (current) load().catch(() => {});
    if (modal.open && $('#auction-detail', modalBody)) refreshDetail().catch(() => {});
  }, 4000);
  return {page, detail, create, stop};
};
