# 网页版：下载链接、浏览器通知与开发者 API（2026-11-06）

状态：**已在本仓库实现并测试，尚未部署到生产**。部署步骤见下文。

接口文档（面向 API 使用者）：[OPEN-API.md](OPEN-API.md)。

## 内容

| 功能 | 说明 |
| --- | --- |
| 下载链接 | 网页版根据浏览器 User-Agent 推荐对应的 GitHub 下载：Android 指向最新 APK，Windows 指向 `-setup.exe`，Linux 指向 `.AppImage`（没有时用 `.deb`），iOS / macOS 等其他系统指向 Releases 页面。网关从公开的 GitHub API 读取 Releases 并缓存 1 小时；读取失败时退回 Releases 页面。桌面版还没有发布时，Windows / Linux 也指向 Releases 页面。登录页和账户菜单都显示。 |
| 浏览器通知 | 标准 Web Push（RFC 8030 / 8291 / 8292），内容为发送者和消息预览，预览规则与 Android 相同；旧版加密会话只显示“你有新的消息”。归档、免打扰的会话和自己发送的消息不推送。点击通知回到对应会话。订阅绑定在网页登录会话上：退出登录即停止推送，重新登录后自动恢复。 |
| 开发者 API | 网页版申请 API Key（每个账户最多 3 个，只显示一次）；只读 REST 接口查询会话列表（含归档）和服务器保存的消息（单次最多 24 小时）；WebSocket 订阅新消息，空闲 30 秒发送一次 ping。限速：同账户 5 次/秒（不按 IP 限速）。 |

## 不影响非网页版本

- 客户端中所有入口都以 `!isApp` 为条件。Android 和桌面版注入 `window.YiChatApp`，因此看不到这些菜单，也不会注册浏览器通知或请求下载信息。
- 服务端的管理接口（`/api/developer/keys*`、`/api/webpush/*`）要求网页登录的 HttpOnly Cookie，如果同时带 Bearer，必须是同一个会话，并且该会话不能属于 App 设备（`native_devices`），否则返回 `403 YICHAT_WEB_ONLY`。
- API Key 只在 `/open/v1/*` 下有效；登录会话不能访问 `/open/v1/*`。客户端不调用这套 API。
- 现有路由、FCM 推送、App 通知、已退役的 `/sw.js` 均未修改。通知 worker 是独立的 `/push/sw.js`，作用域为 `/push/`，没有 `fetch` 处理，不缓存、也不拦截页面请求。
- 数据库只新增两张表（`api_keys`、`webpush_subscriptions`），不修改已有的表。

## 改动的文件

| 文件 | 改动 |
| --- | --- |
| `backend/open_api.py` | 新增：Key 管理、REST、WebSocket、限速 |
| `backend/webpush.py` | 新增：VAPID、aes128gcm 加密、订阅接口、发送循环、`/push/sw.js` |
| `backend/downloads.py` | 新增：`GET /api/downloads` |
| `backend/app.py` | 安装以上三个模块；在 lifespan 中启动 Web Push 发送循环（密钥加载失败只记录警告，不影响网关启动） |
| `backend/db.py` | 在迁移中创建两张新表 |
| `backend/config.py` | 新增 `YICHAT_WEBPUSH_KEY`、`YICHAT_GITHUB_REPO`、`YICHAT_API_DOCS_URL`，都有默认值 |
| `deploy/backup.py` | 私密备份包中增加 VAPID 私钥 |
| `web/push-sw.js` | 新增：通知 worker |
| `client/src/web-tools.tsx` | 新增：下载链接、浏览器通知、API Key 管理界面 |
| `client/src/main.tsx`、`client/src/style.css` | 菜单入口、登录页下载链接；取代原来写死的 0.3.7 APK 链接 |
| `requirements.in` / `requirements.lock` | 新增 `websockets==17.4`（uvicorn 的 WebSocket 实现；没有其他依赖，其余版本不变） |
| `tests/test_open_api.py`、`tests/test_webpush.py`、`tests/test_downloads.py` | 新增测试 |

## 部署步骤（生产目录 `/root/yitiao/yichat`）

1. 备份：`sudo .venv/bin/python deploy/backup.py`，记录当前提交作为回退点。
2. 同步代码；`backend/`、`web/`、`client/src/`、`deploy/backup.py` 等以生产文件为准合并（生产目录可能有未提交的改动）。
3. 安装依赖：`uv pip install --python .venv/bin/python websockets==17.4`（或 `uv pip sync --python .venv/bin/python requirements.lock`）。
4. 构建网页：`cd client && npm ci && npm run build`。
5. 重启：`sudo systemctl restart yichat.service`。首次启动会在 `/var/lib/yichat/gateway/webpush-vapid.pem` 生成 VAPID 私钥（0600）。**这个私钥需要长期保留**：更换后，所有浏览器都要重新开启通知。之后运行的 `backup.py` 会把它放进私密备份包。
6. 检查：`journalctl -u yichat` 中没有 ERROR，也没有 `Web Push unavailable`；`sqlite3 /var/lib/yichat/gateway/chat.db '.tables'` 中有 `api_keys` 和 `webpush_subscriptions`。
7. 只读验证（不需要登录）：
   ```bash
   curl -s https://chat.yitiao.org/api/downloads -A 'Mozilla/5.0 (Linux; Android 14)' | python3 -m json.tool
   curl -s -o /dev/null -w '%{http_code}\n' https://chat.yitiao.org/push/sw.js                 # 200
   curl -s -o /dev/null -w '%{http_code}\n' https://chat.yitiao.org/open/v1/conversations      # 401
   curl -s -o /dev/null -w '%{http_code}\n' https://chat.yitiao.org/api/developer/keys         # 401
   ```
8. 手动验证（网页版，自己的账户）：创建 Key 后调用 `/open/v1/conversations`，用 `websocat` 连接 `/open/v1/stream` 并收到 `ready`；开启浏览器通知后，关闭标签页，让另一个账户发一条消息，确认收到通知并能点击回到会话。
9. App 回归：Android 和桌面版的菜单中没有“浏览器通知”“开发者 API”，原有的通知和功能都正常。

Caddy 不需要修改：`chat.yitiao.org` 会把所有路径（包括 WebSocket 升级请求）转发给网关。Cloudflare 默认支持 WebSocket。

## 回退

代码回退到原提交，重启 `yichat.service`。两张新表和 VAPID 私钥可以保留，不影响旧代码。`websockets` 包可以保留。

## 已知限制

- 不按 IP 限速（2026-11-06 按要求取消）。认证失败的请求直接返回 401，不计数；Key 为 256 位随机值，无法猜测。
- API 文档链接默认指向 `claude/yichat-desktop-planning-sodnv1` 分支；合并到 `main` 后，请修改 `YICHAT_API_DOCS_URL`，或者把默认值改为 `main`。
- iOS / iPadOS 上的网页通知需要 16.4 以上系统，并且要先把网站添加到主屏幕。
