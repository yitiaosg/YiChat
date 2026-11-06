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
