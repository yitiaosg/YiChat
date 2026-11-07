// Debug-only smoke probe (never compiled into release builds).
setTimeout(async () => {
  const r = { origin: location.origin, secure: window.isSecureContext, locks: !!navigator.locks,
    idb: !!window.indexedDB, bc: typeof BroadcastChannel, subtle: !!(crypto && crypto.subtle),
    bridge: !!window.YiChatApp, tauriGlobal: typeof window.__TAURI__, title: document.title,
    text: (document.getElementById('app')?.innerText || '').slice(0, 300) };
  try { await navigator.locks.request('probe', async () => {}); r.lockOk = true } catch (e) { r.lockOk = String(e) }
  try { r.idbOk = await new Promise((res, rej) => { const q = indexedDB.open('probe', 1); q.onupgradeneeded = () => q.result.createObjectStore('s'); q.onsuccess = () => { const tx = q.result.transaction('s', 'readwrite'); tx.objectStore('s').put(new Uint8Array([1]), 'k'); tx.oncomplete = () => res(true); tx.onerror = () => rej(tx.error) }; q.onerror = () => rej(q.error) }) } catch (e) { r.idbOk = String(e) }
  try { const k = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt']); r.keyOk = !!k } catch (e) { r.keyOk = String(e) }
  try { const s = await fetch('/api/session'); r.proxyStatus = s.status; r.proxyBody = (await s.text()).slice(0, 120) } catch (e) { r.proxyStatus = String(e) }
  try { const s = await fetch('/_synapse/admin/v1/x'); r.blockedStatus = s.status } catch (e) { r.blockedStatus = String(e) }
  try { const s = await fetch('https://example.com/'); r.crossOrigin = s.status } catch (e) { r.crossOrigin = 'blocked:' + e.name }
  window.YiChatApp.postMessage(JSON.stringify({ id: 'probe', method: 'debugProbe', args: r }));
}, 6000);
// Phase 2 probe: visibility shim + notification pipeline + click-to-open.
(() => {
  const report = (stage, value) => window.YiChatApp.postMessage(JSON.stringify({ id: 'p2' + stage, method: 'debugProbe', args: { stage, ...value } }));
  const seen = { fetch: [], open: [] };
  // Stand-in for installNotificationContent(): answer with verified-looking content.
  window.addEventListener('yc:notification-fetch', e => {
    seen.fetch.push(e.detail);
    window.YiChatApp.postMessage(JSON.stringify({ id: 'r' + e.detail.id, method: 'notificationResult',
      args: { id: e.detail.id, ok: true, messages: [{ id: '$1', sender: 'Alice', body: '晚上<b>好</b> & hi', time: 1 }, { id: '$2', sender: 'Bob', body: '明天见', time: 2 }] } }));
  });
  window.addEventListener('yc:open-room', e => { seen.open.push(e.detail); report('open', { room: e.detail }); });
  setTimeout(() => {
    const before = document.hidden;
    let changes = 0; document.addEventListener('visibilitychange', () => changes++);
    window.dispatchEvent(new CustomEvent('yc-desktop:visibility', { detail: true }));
    const during = [document.hidden, document.visibilityState];
    window.dispatchEvent(new CustomEvent('yc-desktop:visibility', { detail: false }));
    report('visibility', { before, during, after: document.hidden, changes });
    window.YiChatApp.postMessage(JSON.stringify({ id: 'n1', method: 'debugNotify', args: { room: '!room1:chat.yitiao.org', event: '$evt1', label: '项目群', kind: 'group' } }));
    window.YiChatApp.postMessage(JSON.stringify({ id: 'n2', method: 'debugNotify', args: { room: 'bad-room', event: '$evt1', label: 'x', kind: 'group' } }));
    setTimeout(() => report('fetches', { fetch: seen.fetch }), 3000);
  }, 7000);
})();
setTimeout(() => {
  const port = window.YiChatApp, prev = port.onmessage;
  port.onmessage = e => { const r = JSON.parse(e.data); if (r.id === 'close') { port.onmessage = prev;
    port.postMessage(JSON.stringify({ id: 'p2close', method: 'debugProbe', args: { stage: 'close', ...r.value, hiddenAfterClose: document.hidden } })); } else if (prev) prev(e) };
  port.postMessage(JSON.stringify({ id: 'close', method: 'debugClose', args: {} }));
}, 12000);
// Phase 3 probe: return activation lands on /?order=…; backup methods answer.
setTimeout(() => {
  const port = window.YiChatApp, prev = port.onmessage, out = { stage: 'backup', search: location.search };
  port.onmessage = e => { const r = JSON.parse(e.data);
    if (r.id === 'bs') { out.backupStatus = r.ok ? r.value : r.error; port.postMessage(JSON.stringify({ id: 'rr', method: 'recordRooms', args: {} })); }
    else if (r.id === 'rr') { out.recordRooms = r.ok ? r.value : r.error; port.onmessage = prev;
      port.postMessage(JSON.stringify({ id: 'p3', method: 'debugProbe', args: out })); }
    else if (prev) prev(e) };
  port.postMessage(JSON.stringify({ id: 'bs', method: 'backupStatus', args: {} }));
}, 3000);
