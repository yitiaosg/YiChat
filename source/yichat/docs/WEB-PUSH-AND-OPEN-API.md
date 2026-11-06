# 网页版：下载链接、浏览器通知与开发者 API（2026-11-06）

状态：**已部署到生产（2026-11-06）**，部署记录见文末。

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
   curl -s -o /dev/null -w '%{http_code}\n' https://chat.yitiao.org/api/developer/keys         # 403 YICHAT_WEB_ONLY（没有网页 Cookie）
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

## 生产部署记录（2026-11-06）

- 仓库提交：`042ab59`（功能）+ `bc3b554`（按要求取消 IP 限速）。生产部署分支提交：`5eb9f66` → `4951b6c`。
- 依赖：生产 venv 只新增 `websockets==17.4`，其余包不变。
- 网页：`/var/lib/yichat/web/releases/20261106T151447Z`（上一版本 `20261030T135648Z-doudizhu-counter-shop`）。部署前确认过，生产 `client/src` 不加改动时的构建结果与当时线上版本逐字节一致，所以新版本只包含本次改动。
- 首次启动生成 `/var/lib/yichat/gateway/webpush-vapid.pem`（0600）；`api_keys`、`webpush_subscriptions` 两张表已创建。
- 备份：`/root/yitiao/backups/yichat-v2-20261106T150125Z/`，其中 `webapi-baseline/` 是 8 个被修改文件的原样副本。
- 部署分支提交说明：`backend/app.py`、`backend/db.py`、`client/src/main.tsx`、`client/src/style.css` 中的改动与服务器上原有的未提交改动在同一行或相邻行，无法单独提交，因此只存在于生产工作区；内容与本仓库一致。
- 测试：新增的 66 个后端测试在生产代码上全部通过。生产目录里的 `tests/test_gateway.py` 和 `tests/conftest.py` 比本仓库旧（缺少当前成员屏障的夹具数据），用生产目录的夹具运行时，部分依赖夹具的测试会失败，这不是代码问题；用本仓库的夹具时全部通过。同时修正了 `tests/test_native_wallet_return.py` 中重复插入成员记录的问题，两种夹具下都能通过。生产客户端单元测试中 `native fetch and server FCM summaries…` 一项在部署前后都失败，与本次无关。
- 只读验证：`/api/downloads` 按 UA 返回 Android APK 直链；桌面版尚未发布，Windows/Linux/iOS 指向 Releases 页面。`/push/sw.js` 200；`/open/v1/conversations` 401；`/api/developer/keys`、`/api/webpush/config` 无 Cookie 时 403 `YICHAT_WEB_ONLY`。经 Cloudflare 的 `wss://chat.yitiao.org/open/v1/stream` 能完成 WebSocket 升级，无效 Key 收到 `YICHAT_API_KEY_INVALID` 并以 4401 关闭。journal 无 ERROR/WARNING。

### 回退

```bash
B=/root/yitiao/backups/yichat-v2-20261106T150125Z/webapi-baseline
cd /root/yitiao/yichat
# 1. 网页切回上一版本（原子替换链接）
sudo ln -sfn /var/lib/yichat/web/releases/20261030T135648Z-doudizhu-counter-shop /var/lib/yichat/web/current.next
sudo mv -T /var/lib/yichat/web/current.next /var/lib/yichat/web/current
# 2. 网关代码恢复原文件（新模块文件留着无害，旧 app.py 不会加载它们）
for f in backend/app.py backend/config.py backend/db.py client/src/main.tsx client/src/style.css \
         deploy/backup.py requirements.in requirements.lock; do sudo cp $B/$f $f; done
sudo systemctl restart yichat.service
# 3. 在部署分支记录回退（只改暂存区）
sudo git -C /root/yitiao show 4951b6c | sudo git -C /root/yitiao apply --cached -R -C1 && \
  sudo git -C /root/yitiao commit -m "Revert Web download/push/open API deploy (4951b6c)"
```

两张新表、VAPID 私钥和 `websockets` 包可以保留。

## 扩展：发送、附件、单会话订阅、Key 权限和撤回（2026-11-06，**已部署到生产**，记录见本节末尾）

接口文档已同步更新：[OPEN-API.md](OPEN-API.md)。

| 功能 | 说明 |
| --- | --- |
| 消息转为文字 | 消息对象新增 `kind`、`attachment`、`share`。转账、红包、收款、赠卡、站内分享（本质为网址）由服务器转换为文字，例如 `[转账] 0.01 YIDT`、`[红包] 1 YIDT · 3 个 · 恭喜发财`、`[分享] YIDT 交易`；图片、文件、语音、视频显示为 `[图片] 文件名` 等。订单只在同一会话内显示金额和状态。 |
| 下载附件 | `GET /open/v1/files/{id}`：每次请求下载一个文件，访问规则与网页版下载相同（上传者，或作为成员看到过引用该文件的消息），到期返回 410。 |
| 发送 | `POST /open/v1/conversations/{id}/messages`（文字，最多 2000 字符）和 `POST /open/v1/conversations/{id}/files`（图片或文件，原始字节，最大 100 MB，一个请求完成上传和发送）。支持 `txn_id` 幂等重试。私聊和群聊都可以；旧版加密会话不可以。 |
| 限速 | 读取、发送、上传、下载、WebSocket 连接共用同一个账户限额（5 次/秒）。上传另外与网页版和 App 共用账户的上传额度（单文件 100 MB、每天 1 GiB、速度和并发限制）。 |
| 撤回 | `DELETE /open/v1/conversations/{id}/messages/{message_id}`，需要读写 Key。规则与网页版一致：自己的消息 5 分钟内可撤回；群聊中只有群主能撤回其他成员的消息（原因“群主撤回成员消息”），管理员不能；转账、红包、赠卡不可撤回。网关先按同样规则检查并返回明确的错误码，Synapse 模块的 `validate_redaction` 仍是最终判断，其错误码原样透传。撤回事件同样通过一次性授权放行，授权绑定撤回目标的事件 ID。 |
| Key 权限 | 每个 Key 为“只读”（默认）或“读写”，在网页版创建时选择、之后随时修改（`PATCH /api/developer/keys/{id}`，仅网页会话），立即生效。只有读写 Key 能发送，只读 Key 调用发送接口返回 `403 YICHAT_API_KEY_READ_ONLY` 且不上传数据。改回只读时作废未使用的发送授权；Synapse 模块的授权检查也要求 Key 仍为读写。迁移时已有的 Key 一律为只读（它们创建时 API 本来就是只读的）。 |
| WebSocket | `?conversation=<会话ID>` 只订阅一个会话（私聊或群聊）；不是成员时以 `4403` 关闭。并发限制从“每个 Key 1 个”改为“**每个账户 3 个**”，第 4 个以 `4409 YICHAT_STREAM_LIMIT` 关闭。 |

### 发送的实现与安全模型变化

- 每个 Key 第一次发送时，网关用账户已有的 Matrix 凭据为它登录一个专用设备“YiChat API · Key 名称”，令牌加密保存在 `api_keys.upstream`。删除 Key 时通过 Synapse 管理接口登出该设备。
- 消息经过与 App 相同的网关检查：当前成员、`policy.require_send`（禁言、解散、注销、私聊隐私）、`policy.validate_event`（字数）、附件归属。上传复用网页版的上传实现（`store_upload`），下载复用 `serve_file`；两者从原来的闭包中提取出来，网页版和 App 的行为不变。
- **Synapse 模块的变化**：原规则要求发送者必须有一个有效的主站登录会话。API 发送没有这样的会话，因此模块现在也接受网关预先登记的**一次性授权**（`backend/api_grants.py`，表 `api_send_grants`）：授权绑定会话、发送者、事件类型和完整内容的摘要，2 分钟内有效，所属 Key 删除后立即失效，发送成功后标记为已使用。只适用于 `m.room.message`；其后的所有检查不变。
- 这意味着：**持有 API Key 即可在用户未登录网页或 App 时以其名义发消息**（读取原本就是如此）。主站账户注销或 Key 删除后立即失效。

### 改动的文件

| 文件 | 改动 |
| --- | --- |
| `backend/open_api.py` | 消息文字转换、下载、发送文字/文件、Key 专用设备、单会话订阅、每账户 3 个订阅 |
| `backend/api_grants.py` | 新增：一次性发送授权 |
| `backend/synapse_module.py` | 无主站会话时接受匹配的一次性授权（消息按完整内容，撤回按目标事件 ID） |
| `backend/app.py` | 上传、下载核心提取为 `store_upload`、`serve_file`；API 上传路径加入上传大小例外 |
| `backend/limits.py` | API 上传路径使用文件大小上限 |
| `backend/db.py` | 调用 `open_api.migrate()`：`api_keys` 增加 `device`、`upstream`、`scope`（默认 `read`）三列，新增 `api_send_grants` 表 |
| `client/src/web-tools.tsx` | 创建 Key 时选择权限、在列表中修改权限（改为读写需确认）、说明文字 |
| `tests/test_open_api.py` | 新增发送、下载、文字转换、授权、订阅限制等测试 |
| `docs/OPEN-API.md` | 接口文档 |

### 部署步骤

与上一节相同（备份 → 同步文件 → 构建并发布网页 → 重启），另外：

1. 本次修改了 Synapse 模块，**需要同时重启 `yichat-synapse` 和 `yichat`**（先 Synapse，再网关）。迁移在网关启动时自动执行。
2. 不需要新依赖，不需要修改 Caddy。
3. 只读验证：`curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer yck_invalid' https://chat.yitiao.org/open/v1/conversations/x/messages` 应返回 `401`（不带 `Authorization` 也不带 `Origin` 的 POST 会先被来源检查拒绝，返回 `403`）；`curl … https://chat.yitiao.org/open/v1/files/0` 应返回 `401`。
4. 手动验证（自己的账户）：先用只读 Key 调用发送接口，确认返回 403；在网页版把它改为读写后再用 API 发送文字、图片、文件到一个私聊和一个群聊，在 App 中确认收到、图片能显示、文件能下载；用 API 撤回自己刚发的一条消息，以及（作为群主）撤回群成员的一条消息，确认 App 中显示已撤回，超过 5 分钟的消息返回 `YICHAT_REDACTION_WINDOW`；查询消息时转账和红包显示为文字；在网页版删除 Key 后，设备列表中的“YiChat API”设备消失，再用该 Key 发送返回 401。

### 回退

代码和网页回退到上一版本，重启 `yichat-synapse` 和 `yichat`。新增的列和表可以保留。回退后已发出的消息不受影响；Key 专用的 Matrix 设备会保留在设备列表中，可在删除 Key 前先回退，或之后在 App 的设备列表中手动登出。

### 生产部署记录（2026-11-06）

- 仓库提交：`6c4a029`（发送、附件、文字转换、单会话订阅）+ `8e51ec2`（Key 权限）+ `bc0d460`（撤回）。生产部署分支：`2966ace` → `f20cef3`。
- 部署前确认：生产上 10 个相关文件都与上次部署的版本一致，直接替换，没有合并；生产网页源码不加改动时的构建与线上版本逐字节一致。
- 测试（生产代码 + 本仓库夹具）：相关 8 个测试文件共 232 个，全部通过；Synapse 模块在生产 venv 中导入正常。
- 重启顺序：`yichat-synapse`（约 4 秒恢复）→ `yichat`。两者 journal 中均无 ERROR、WARNING、Traceback，`/health` 为 `ok`。
- 迁移：`api_keys` 已有 `device`、`upstream`、`scope` 列；`api_send_grants` 表已创建。生产上还没有 API Key。
- 网页：`/var/lib/yichat/web/releases/20261106T164504Z`（上一版本 `20261106T151447Z`）。
- 备份：`/root/yitiao/backups/yichat-v2-20261106T163813Z/`，其中 `api2-baseline/` 是 9 个被修改文件的原样副本（`api_grants.py` 为新增）。
- 部署分支提交说明：`backend/app.py`、`backend/db.py` 中的改动与服务器上原有的未提交改动重叠，只存在于生产工作区，内容与本仓库一致；其余文件已提交。
- 只读验证（经 Cloudflare）：发送、上传、撤回、下载在 Key 无效时返回 401；不带认证也不带 Origin 的 POST 返回 403；无网页 Cookie 修改 Key 权限返回 403 `YICHAT_WEB_ONLY`；`wss://…/open/v1/stream?conversation=…` 在 Key 无效时以 4401 关闭。

#### 回退

```bash
B=/root/yitiao/backups/yichat-v2-20261106T163813Z/api2-baseline
cd /root/yitiao/yichat
# 1. 网页切回上一版本
sudo ln -sfn /var/lib/yichat/web/releases/20261106T151447Z /var/lib/yichat/web/current.next
sudo mv -T /var/lib/yichat/web/current.next /var/lib/yichat/web/current
# 2. 恢复 9 个原文件（新增的 backend/api_grants.py 留着无害）
for f in backend/app.py backend/db.py backend/limits.py backend/open_api.py backend/synapse_module.py \
         client/src/web-tools.tsx docs/OPEN-API.md docs/WEB-PUSH-AND-OPEN-API.md tests/test_open_api.py; do sudo cp $B/$f $f; done
# 3. 先 Synapse，再网关
sudo systemctl restart yichat-synapse.service && sudo systemctl restart yichat.service
# 4. 在部署分支记录回退（只改暂存区）
sudo git -C /root/yitiao show f20cef3 | sudo git -C /root/yitiao apply --cached -R -C1 && \
  sudo git -C /root/yitiao commit -m "Revert developer API send/recall deploy (f20cef3)"
```

新增的列和表可以保留。回退前如已有 Key 用于发送，请先在网页版删除这些 Key，让对应的“YiChat API”设备被登出；回退后旧代码不会再登出它们。

