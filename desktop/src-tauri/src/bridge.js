// YiChat desktop: exposes the existing `window.YiChatApp` message port (bridge v1)
// on the shell origin's top frame only. Messages travel through one Tauri
// command; no other native API is granted to the page.
(() => {
  'use strict';
  const ORIGIN = '__YICHAT_ORIGIN__';
  if (window.top !== window || location.origin !== ORIGIN) return;
  const internals = window.__TAURI_INTERNALS__;
  if (!internals || typeof internals.invoke !== 'function') return;
  const invoke = internals.invoke.bind(internals);
  const port = {
    onmessage: null,
    postMessage(message) {
      if (typeof message !== 'string' || message.length > 400000) return;
      invoke('yichat_bridge', { message }).then(reply => {
        const handler = port.onmessage;
        if (typeof reply === 'string' && reply && typeof handler === 'function') handler({ data: reply });
      }, () => {});
    },
  };
  Object.defineProperty(window, 'YiChatApp', { value: port, writable: false, configurable: false, enumerable: false });

  // Desktop-only notice when no OS secure store is available (Linux without
  // Secret Service): nothing is persisted, so say why. Shared UI is untouched.
  const status = () => invoke('yichat_bridge', { message: JSON.stringify({ id: 'desktop-status', method: 'desktopStatus', args: {} }) })
    .then(raw => {
      const reply = JSON.parse(raw);
      if (!reply.ok || reply.value.persistent) return;
      const bar = document.createElement('div');
      bar.setAttribute('role', 'status');
      bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:8px 14px;font:13px/1.5 system-ui,sans-serif;background:#7a2e0e;color:#fff;text-align:center';
      bar.textContent = (reply.value.reason || '系统安全存储不可用') +
        '。为保护数据，本次登录状态和聊天缓存不会保存到磁盘，退出后需要重新登录。启用并解锁系统密钥服务后重启 YiChat 即可保存。';
      document.body.appendChild(bar);
    }).catch(() => {});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', status, { once: true });
  else status();
})();
