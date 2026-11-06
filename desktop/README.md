# YiChat 桌面版（Windows / Linux）

Tauri 2 外壳，界面沿用 `source/yichat/client` 的共享 Preact 前端，通过已有的 `window.YiChatApp` 消息端口（bridge v1）与原生侧通信。共享前端和 Android 代码均未修改。

> 说明：自 0.3.4 起聊天已停用端到端加密，服务器可读取消息。桌面端保证的是：令牌、设备凭据和本地数据只存于本机安全存储或加密存储，不写入日志，也不发往网关以外的任何地方。

## 架构

```
WebView (yichat://localhost  |  https://yichat.localhost on Windows)
  │  共享前端 index.html + /assets/*      ← ShellCache：缓存 → 内置 → 线上
  │  fetch /api/ /_matrix/ /auth/        ← 反向代理，仅转发到 https://chat.yitiao.org
  │  window.YiChatApp.postMessage(...)   ← 唯一 Tauri 命令 yichat_bridge
  ▼
Rust: bridge.rs（会话/登录/锁定/退出/下载保存） auth.rs（PKCE + RFC 8252 回环）
      secure_store.rs（Windows 凭据管理器 / Linux Secret Service）
```

- **同源**：页面以本地源加载，前端的同源检查与 Matrix SDK `baseUrl=location.origin` 无需改动。
- **代理白名单**：只转发 `/api/`、`/_matrix/`、`/auth/` 到固定的 `https://chat.yitiao.org`；拒绝 `..`、`//`、编码分隔符及 `/auth/native/*`（原生登录只由 Rust 执行）。不转发、不保存 Cookie，去掉 `Origin/Referer/Cookie` 等头，同源 `Location` 改写为相对路径。
- **混合外壳**：首页先用缓存或内置版本秒开；后台下载线上 `index.html`，只有其 `<meta name="yichat-app-bridge" content="N">` 版本在 `BRIDGE_VERSIONS` 中、且所有引用资源都下载成功时，才在下次启动采用。否则继续使用内置版本。缓存与桌面版本号绑定，应用升级后从内置版本重新开始。认证 API 与媒体从不进入缓存。
- **权限**：`capabilities/main.json` 只授予 `allow-yichat-bridge`；不包含 fs/shell/http/opener 插件，也不授予 `core:default`。`withGlobalTauri=false`。命令还会校验窗口标签与页面来源。
- **导航**：只允许本地源的 `/`；`/auth/login` 触发原生登录；其他 http(s) 链接交给系统浏览器；新窗口与 WebView 下载一律拒绝。
- **安全存储**：会话、设备恢复凭据和待撤销令牌只保存在系统安全存储中。Linux 没有 Secret Service 时，WebView 改为无痕模式，所有内容只保存在内存里，并在页面底部提示原因。

## 登录流程

1. 前端调用 `appCall('login')`；Rust 在 `127.0.0.1:<随机端口>` 监听，并用系统浏览器打开 `/auth/native/login?...&redirect_uri=http://127.0.0.1:<port>/app/callback&platform=windows|linux`。
2. 浏览器回跳到回环地址；Rust 校验路径、参数个数和 state（常量时间比较），返回不含请求内容的静态页面。
3. 用 code + verifier 调用一次 `/auth/native/exchange`，校验 `homeserver`、uid 和 mxid 后写入安全存储，然后重新载入页面。

此流程依赖服务端提交 `Gateway: accept RFC 8252 loopback redirect for desktop native login`。

## 开发与测试

依赖：Node 22+、Rust 1.80+；Linux 还需要 `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libssl-dev libdbus-1-dev`。

```bash
cd desktop
npm install
npm run web                      # 构建共享前端到 source/yichat/client/dist（内置外壳）
cd src-tauri
cargo test                       # 单元测试（代理白名单、回环回调、外壳刷新等）
cargo test -- --ignored          # 需要已解锁的 Secret Service / Windows：安全存储读写
cargo run                        # 启动开发版
```

调试版冒烟探针（仅 debug 构建、需要环境变量）：`YICHAT_DESKTOP_PROBE=1 cargo run`。约 6 秒后，标准错误会打印 `YICHAT_PROBE {...}`，内容包括安全上下文、Web Locks、IndexedDB、AES-GCM、bridge、代理状态、白名单拦截和跨源拦截的检测结果。

## 阶段

1. ✅ 外壳、加载界面、本地协议、反向代理、混合缓存、原生登录、会话/锁定/退出、文件保存
2. ☐ 托盘与通知（关闭到托盘、Toast/libnotify、点击打开会话、预览开关、单实例、开机自启、断线重连）
3. ☐ 本地加密备份（SQLCipher + 系统安全存储、历史补拉、记录核验、YCB1 导出）
4. ☐ 打包与 CI（NSIS 并注册 AppUserModelID、AppImage、.deb、带签名的自动更新、GitHub Actions）
