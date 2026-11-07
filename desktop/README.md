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

## 托盘、通知与后台（第 2 阶段）

- **托盘**：菜单有“打开 YiChat”“通知显示消息预览”“开机时启动”“退出 YiChat”。点击窗口关闭按钮只是隐藏到托盘，页面和 Matrix 同步连接继续运行；第一次隐藏时会提示一次。如果桌面环境没有托盘（例如 Linux 缺少 AppIndicator），关闭窗口就是退出。
- **通知流程**：与 Android 相同。共享前端在实时消息到达时调用 `notifyRoom`（它已跳过静音、归档、只读和当前打开的会话）；原生侧再检查一次，窗口在前台且正是该会话时不弹通知。
  - 开启预览：原生派发 `yc:notification-fetch`，页面用已有的 `notificationContent()` 校验成员、未读和撤回后，通过 `notificationResult` 返回正文。通知标题为会话名，群聊每行是“发送者: 内容”，最多 4 行，超过时显示条数。
  - 关闭预览：标题固定为 `YiChat`，内容为“你有新消息”，不会向页面请求正文。
  - 每个会话只保留一条通知，原地替换；会话已读、关闭通知、锁定或退出时撤下。正文只在内存中经过，不写日志，也不写磁盘。
- **点击通知**：显示窗口，并通过 `yc:open-room` 打开对应会话；页面尚未就绪时，由 `pendingNotification` 在启动后补开。
  - Linux：用 D-Bus `org.freedesktop.Notifications` 的 default action（libnotify 协议）；正文做了标记转义。
  - Windows：WinRT Toast，`activationType="protocol"`，启动参数为 `yichat-open://room?id=…`。单实例插件把它转发给正在运行的实例；应用已退出时则带着这个参数启动。启动时会在 HKCU 登记 AppUserModelID（`org.yitiao.yichat.desktop`）和 `yichat-open:` 协议，不需要管理员权限；安装包也会登记同样的项（第 4 阶段）。
- **单实例**：再次启动时只会激活已有窗口。
- **开机自启**：默认关闭，在托盘菜单中切换（Windows 写入 HKCU Run，Linux 写入 `~/.config/autostart`），以 `--autostart` 启动时直接进入托盘。
- **断线重连**：SDK 自己会退避重试。原生每 15 秒探测一次网关；网络恢复或检测到系统从睡眠唤醒时，派发 `yc:notification-sync`，让 SDK 立即重试。WebView2 关闭了后台计时器节流。
- **页面可见性**：共享前端只在 `document.hidden` 时为当前会话发通知。窗口隐藏到托盘后，桌面 shim 会让 `document.hidden` 为 true，Windows 和 Linux 行为一致。
- **设置**：保存在 `<配置目录>/settings.json`，内容只有通知开关、预览开关和提示是否已显示；默认开启通知并显示预览，与 Android 新版一致。

## 本地加密备份（第 3 阶段）

- **数据库**：每个账户一个 SQLCipher 库 `<数据目录>/accounts/<storage>/history.db`，整库加密，房间 ID、时间等元数据也包含在内。密钥是随机 256 位，只存在系统安全存储中（`db-key.<storage>`）。没有安全存储时数据库只在内存里，不落盘。如果找不到密钥，原有文件会改名留存，不会被静默删除。`storage` 的计算方式与 Android 相同：`sha256(origin|uid|mxid|device)`。
- **补拉**：移植自 Android 的 `HistoryRepository`。登录时以及之后每分钟，从 `/api/history/updates` 取服务器认证摘要，从 `/api/history/packets` 取原消息包。只有满足以下全部条件才接收：末尾是 LF、大小合规、SHA-256 与摘要一致、事件和房间身份一致、类型一致。撤回的消息记为墓碑。只有当前窗口完整提交、且没有待补的包时，水位才前进。消息 epoch 与 Web/Android 一致，epoch 变化时清空本地库。
- **成员补齐**：服务器已清理的包，会按原协议请求其他成员补齐，可以接收他们转发的包。**桌面端不会响应其他成员的请求**（不向服务器上传本机保存的明文原包），这一点与 Android 不同。
- **核验聊天记录**：`recordRooms/recordDays/recordPage` 移植自 Android 的 `RecordAudit`。按 UTC 日计算 `yichat-record-digests-v1` 摘要，规范 JSON 与网关的 `archive.canonical` 逐字节一致（键排序、紧凑格式、`ensure_ascii=False`，有专门测试）。
- **内部快照**：每 6 小时（或点击“立即保存内部快照”时）用 `sqlcipher_export` 生成同一密钥加密的副本 `history.snapshot.db`，磁盘上不会出现明文。
- **外部导出**：与 Android 相同的 YCB1 格式（`YCB1 | salt32 | iv12 | AES-256-GCM`，scrypt N=32768/r=8/p=1，固定 AAD），内容是 `yichat-app-packets` v1。已用独立的 Python 实现验证可以互相解密。导出目录、口令和间隔保存在系统安全存储中；第一次导出在设定的间隔之后执行，与 Android 一致。没有安全存储时不能启用导出。
- **钱包和付款回跳**：网关的 `/app/desktop-return` 转交 `yichat-open://return[?order=<32位>]`，桌面端把页面导航到 `/` 或 `/?order=…`，由共享前端查询订单，并要求再次确认出资。格式不合法的回跳地址会被忽略。

## 打包、发布与自动更新（第 4 阶段）

| 平台 | 产物 | 说明 |
| --- | --- | --- |
| Windows | `YiChat_<版本>_x64-setup.exe`（NSIS） | 为当前用户安装，不需要管理员权限，安装界面支持简体中文和英文。开始菜单和桌面快捷方式的 AppUserModelID 是 `org.yitiao.yichat.desktop`（Tauri 模板设置）。安装钩子 `windows/hooks.nsh` 会在 HKCU 登记 AppUserModelId 的 DisplayName 和 IconUri（`toast-icon.png`），所以 Toast 即使没有快捷方式也能显示名称和图标。`yichat-open:` 协议来自 `plugins.deep-link.desktop.schemes`。卸载时清理 AppUserModelId、协议和开机自启项。 |
| Linux | `YiChat_<版本>_amd64.AppImage` | 首次运行时为当前用户写入 `~/.local/share/applications/yichat-desktop-appimage.desktop`，注册 `x-scheme-handler/yichat-open`。 |
| Linux | `YiChat_<版本>_amd64.deb`（包名 `yi-chat`） | 桌面文件使用自定义模板 `linux/yichat-desktop.desktop`，`Exec` 带 `%u`，`MimeType` 包含 `x-scheme-handler/yichat-open`；托盘依赖 `libayatana-appindicator3-1` 由 Tauri 自动添加。 |

rpm 暂不构建。

**Windows 代码签名（预留，未启用）**：`bundle.windows.certificateThumbprint`、`digestAlgorithm` 和 `timestampUrl` 都在 `tauri.conf.json` 里。CI 检测到仓库变量 `WINDOWS_SIGN_COMMAND`（带 `%1` 的签名命令，例如 signtool 或 Azure Trusted Signing）时，会把它写进 `bundle.windows.signCommand`；未设置时不签名。

**自动更新**：
- 使用 `tauri-plugin-updater`，minisign 签名，开启 `requireSignedVersion`，拒绝降级到旧版本的签名包。Windows 用 passive 模式安装；Linux 的 AppImage 和 .deb 由插件按安装类型选择对应条目。
- 仓库中的 `plugins.updater.pubkey` 留空；CI 构建时用仓库变量 `YICHAT_UPDATER_PUBKEY` 通过 `--config` 写入。Tauri CLI 会用它校验签名私钥是否匹配。没有公钥的构建不会自动更新。托盘的“检查更新”在这种构建里改为打开发布页面。
- 程序启动 60 秒后检查一次，之后每 6 小时检查一次。发现新版本时弹窗询问，用户同意才下载；签名校验通过后安装并重启。同一次运行中被拒绝的版本不会再次提示。
- 更新清单固定在 `https://github.com/yitiaosg/YiChat/releases/download/desktop-updater/latest.json`。桌面版的发布**不会**标记为“latest”，“latest”仍然留给 Android。

**首次配置**（只需一次，私钥不进仓库）：

```bash
cd desktop && npx tauri signer generate -w ~/.tauri/yichat-desktop.key   # 按提示设置密码
```

把以下三项加到 GitHub 仓库的 Settings → Secrets and variables → Actions：
- Variables：`YICHAT_UPDATER_PUBKEY`，值为生成的 `.key.pub` 文件内容。
- Secrets：`TAURI_SIGNING_PRIVATE_KEY`，值为 `.key` 文件内容。
- Secrets：`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`，值为上面设置的密码。

私钥丢失后，已安装的客户端就无法再接收自动更新，只能手动安装新版本。请离线备份私钥。

**CI**（`.github/workflows/desktop.yml`）：
- 推送或 PR 改动 `desktop/`、共享前端或这个工作流文件时，在 Windows 和 Ubuntu 22.04 上执行 clippy（警告视为错误）、单元测试和打包，安装包作为 artifact 上传。
- 推送 `desktop-v<版本>` 标签时，先检查标签与 `tauri.conf.json`、`Cargo.toml` 的版本是否一致，并要求更新密钥已配置；然后发布 GitHub Release（包含安装包、`.sig`、`SHA256SUMS.txt` 和 `latest.json`），并刷新 `desktop-updater` 发布中的 `latest.json`。

**发版步骤**：
1. 把 `src-tauri/tauri.conf.json` 和 `src-tauri/Cargo.toml` 的版本号改成新版本。
2. 运行 `cargo update -p yichat-desktop`，然后提交。
3. 执行 `git tag desktop-v<版本> && git push origin desktop-v<版本>`。

## 登录流程

1. 前端调用 `appCall('login')`；Rust 在 `127.0.0.1:<随机端口>` 监听，并用系统浏览器打开 `/auth/native/login?...&redirect_uri=http://127.0.0.1:<port>/app/callback&platform=windows|linux`。
2. 浏览器回跳到回环地址；Rust 校验路径、参数个数和 state（常量时间比较），返回不含请求内容的静态页面。
3. 用 code + verifier 调用一次 `/auth/native/exchange`，校验 `homeserver`、uid 和 mxid 后写入安全存储，然后重新载入页面。

此流程依赖服务端提交 `Gateway: accept RFC 8252 loopback redirect for desktop native login`。

## 开发与测试

依赖：Node 22+、Rust 1.82+；Linux 还需要 `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libssl-dev libdbus-1-dev`。

```bash
cd desktop
npm install
npm run web                      # 构建共享前端到 source/yichat/client/dist（内置外壳）
cd src-tauri
cargo test                       # 单元测试（代理白名单、回环回调、外壳刷新等）
cargo test -- --ignored          # 需要已解锁的 Secret Service / Windows：安全存储读写；YCB1 与 Python 互通（PYTHON=带 cryptography 的解释器）
cargo run                        # 启动开发版
```

调试版冒烟探针（仅 debug 构建、需要环境变量）：`YICHAT_DESKTOP_PROBE=1 cargo run`。标准错误会打印若干行 `YICHAT_PROBE {...}`：
- 约 6 秒：安全上下文、Web Locks、IndexedDB、AES-GCM、bridge、代理状态、白名单拦截和跨源拦截的检测结果；
- 约 7 秒：可见性 shim 的检测结果，并在不需要登录的情况下走一遍通知流程（用模拟正文代替页面），通知被点击后打印 `open`；
- 约 12 秒：模拟关闭窗口，检查窗口是否隐藏到托盘。

Windows 交叉检查（需要 mingw-w64）：`rustup target add x86_64-pc-windows-gnu && cargo clippy --target x86_64-pc-windows-gnu`。

## 阶段

1. ✅ 外壳、加载界面、本地协议、反向代理、混合缓存、原生登录、会话/锁定/退出、文件保存
2. ✅ 托盘与通知（关闭到托盘、Toast/libnotify、点击打开会话、预览开关、单实例、开机自启、断线重连）
3. ✅ 本地加密备份（SQLCipher + 系统安全存储、历史补拉、记录核验、YCB1 导出、钱包回跳）
4. ✅ 打包与 CI（NSIS 并注册 AppUserModelID、AppImage、.deb、带签名的自动更新、GitHub Actions）
