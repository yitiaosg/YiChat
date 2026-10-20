# YiChat Web 构建、部署与回滚

Web 源码在 `yichat/client`：Preact + TypeScript + Vite，原生 `matrix-js-sdk.initRustCrypto()`。不使用 Telegram 代码。生产继续由 Caddy → 5018 网关提供同源页面、SDK 与业务请求，8018 不公开。

## 构建

需要 Node 22.12+、npm 10+；精确依赖由 package-lock.json 固定。

```sh
cd /path/to/reviewed-checkout/yichat/client
npm ci --no-audit --no-fund
npm test
npm run build
```

`prebuild` 从主站复制现有 YCard 渲染器和共用跳伞场景布局；SVG 来自原游戏。结果是 `dist/index.html`、带内容哈希的 JS/CSS/WASM 和轻量只读渲染资源。`dist`、node_modules、浏览器截图和测试密钥不进入 Git。运行服务不需要 Node。

浏览器需要 HTTPS、IndexedDB、Web Crypto、Web Locks；录音渐进支持 MediaRecorder/麦克风权限。仅 Chromium 已进行自动化实测；其他浏览器见实际验收记录。

## 部署

先检查生产 Git 工作树干净，备份原配置、密钥、数据库，确认当前提交。不要覆盖未提交改动，不复制测试数据库或临时密钥。已有 `deploy/backup.py` 可在线备份；它不代替跨库灾难恢复的一致性停机方案。

1. 在独立工作树完成构建和所有检查，将审核后的代码以 fast-forward 部署到 `/root/yitiao`。
2. 将 `client/dist` 复制到 `/var/lib/yichat/web/releases/<UTC版本>/`。新版本的 assets 目录保留上一版本的哈希资源，防止已经打开的标签页延迟加载旧 chunk 时遇到 404。
3. 以原子 rename 将 `/var/lib/yichat/web/current` 符号链接切到新版本。
4. 指定 `YICHAT_WEB_ROOT=/var/lib/yichat/web/current`，或令默认路径 `/root/yitiao/yichat/client/dist` 指向该链接。配置只放服务器端，不把任何生产 env 或密钥放进 Vite。
5. 重启 `yichat`、`website` 和 `yidt`（后者精确放行授权返回聊天域名的 CSP）。重启 `yichat-synapse` 以加载资料更新的成员屏障修复，并由已有启动恢复逻辑核对本写入者遗留 pending；不修改 Caddy 站点块。确认 Caddy 原有配置仍有效。
6. 检查首页、JS/CSS/WASM 的 HTTPS 状态、CSP、静态缓存、`/health`、管理 status/reconcile 及服务 journal。API/HTML no-store；哈希资源 immutable 一年；只读渲染器缓存一小时。

已提供只负责静态文件的原子发布脚本（在部署源码提交后执行）：

```sh
sudo /root/yitiao/yichat/.venv/bin/python /root/yitiao/yichat/deploy/promote_web.py \
  --dist /path/to/reviewed-checkout/yichat/client/dist
sudo systemctl restart yichat-synapse yichat website yidt
```

本轮只新增 SQLite `room_requests` 表，关联原创建票据以便核实响应丢失的请求。没有资金或归档规则迁移，没有改写原始聊天、UID、MXID、密钥或封存文件。退款响应格式修正为文档约定的最小单位 `refund.principal`，同时附带十进制 `refund.amount`；不改变账本金额。

## 回滚

保留部署前提交及前一个静态 release。静态 UI 可原子切回已知版本并重启网关；代码回退使用已审核的提交。若回到占位页，同时回退主站 `chat_available` 和发消息入口。新增表可以保留，旧服务忽略它；不要恢复旧数据库去覆盖新消息、财务队列或归档，不删除浏览器 IndexedDB。

本次发布的确定回滚点为 `31b9070`（原占位页），新客户端 release 为 `/var/lib/yichat/web/releases/20261019T180158Z`，源码实现为 `92a103c`。若确需恢复原代码，先确认工作树干净并做新备份，再执行下列命令；有未提交改动时停止，不使用 force/reset：

```sh
sudo git -C /root/yitiao status --short
sudo git -C /root/yitiao switch --detach 31b9070
sudo systemctl restart yichat-synapse yichat website yidt
```

此操作仅切换已跟踪源码，同时恢复原主站入口/状态；保留线上数据库、新增表、静态 release 和浏览器密钥。它也会恢复旧版本的已知成员屏障问题，因此不应作为单独修复资金问题的手段。恢复本轮版本可切回已部署的 `deploy/yichat-server-v2` 分支并重启上述服务；发布记录确认后续提交仅为文档时不需重新构建。

如需暂停资金，使用现有服务端开关并重启网关，保留订单和出金队列。不要通过前端隐藏按钮当作资金关停。

## 本地隔离验证

```sh
cd /path/to/reviewed-checkout
npm ci --prefix yichat/tests/browser
PYTHONPATH=. /root/yitiao/yichat/.venv/bin/python -m pytest -q yichat/tests
```

完整浏览器测试启动临时 Synapse、原模块、真实网关、原 OIDC 提供者、隔离 SQLite/PG、Chromium 与 Rust WASM。PG 测试 DSN 来自私有 `~/.cache/yichat-test-dsns.json`，必须指向专用测试库；这些测试会清空该测试 schema，不得使用生产 DSN。OIDC 测试需要主站已有 joserfc 1.7.4；本机从 `/opt/vps-python` 补齐测试依赖。所有授权状态注入、测试登录和控制端点只定义于 `tests/test_web_client.py`，不安装到生产服务。

浏览器以域名请求转发到 loopback 的原 YIDT Flask 服务，实际点击授权页并提交 CSRF 表单，不访问生产钱包。资金在临时账本上检查明确 fund/claim/receive、原幂等键和权威交易数量；生产只做只读核查。429、丢失响应、410/503 的浏览器故障注入与实际后端策略测试分别记录，不能将注入称作真实到期。

## 本地数据与隐私

Rust 数据库按 UID、MXID、device_id 的 SHA-256 前缀隔离，使用 32 字节本地存储加密键。Outbox 使用不可导出的本地 AES-GCM 包装密钥加密；资金/赠卡幂等记录按 UID 隔离，重新授权后仍可处理原操作。会话 token 只在运行内存/HttpOnly cookie，不能进入 URL 或构建配置。

一个浏览器 origin 只允许一个活动客户端。第二个标签页可请求接管，原标签页停止 SDK、取消请求并释放加密库。退出撤销网关会话、停止同步、撤销 Blob URL、清空预览/资料缓存和内存密钥，并锁定本地界面。不会擅自删除原设备、加密数据库、下载归档或备份。恢复密钥仅本地显示/下载；服务器密钥备份及 Secret Storage 由 Matrix SDK 加密后上传。


## 2026-10-20 增量版本

本轮增加网关 SQLite 的 `history_receipts`（小元数据/可信哈希）及 `history_meta`，账本 SQLite 的 `yichat_gift_renderings`（赠卡时渲染快照）。不迁移资金余额，不修改既有归档格式/摘要。升级顺序：备份 → 干净工作树 fast-forward → `python -m backend.manage migrate` → 原子发布静态构建 → 重启 `yichat-synapse`、`yichat` 和 `yichat-archive`（后者加载清理前保留单包摘要逻辑）。主站/YIDT代码未改时不需要重启它们。

先迁移再重启 Synapse，因为原生模块也要写新的哈希元数据表。模块自动分批回填仍保留的事件摘要，回填完成前 history 接口503，不把空白回填区标成同步完成。退出锁定与原Rust Crypto数据库标识保持不变。

本轮的上一版本源码为 `307c383`，静态版本为 `/var/lib/yichat/web/releases/20261019T180158Z`。如需回退，先备份并确认 Git 干净，在源码切回该提交后，把 `current` 原子链接指回上述目录，重启网关、Synapse和归档worker；新元数据表/卡面快照保留，不恢复旧数据库覆盖新消息、订单或赠卡。回退会取消本轮新增的服务端5分钟/业务消息撤回规则，应记录这一行为变化。

## 会话资金气泡版本

当前增量的流程、Web/App 边界、实际测试和回滚点见 [WEB-MONEY-BUBBLES-20261020.md](WEB-MONEY-BUBBLES-20261020.md)。本轮没有 schema 或 Synapse 模块变更，只需发布静态资源并重启网关。

## 未入金订单取消与Android交接版本

本次增加未入金订单真实取消（含YIDT授权作废），没有schema/Synapse变更；发布静态资源并重启网关即可。接口、测试及确定回滚点见 [WEB-CANCEL-20261020.md](WEB-CANCEL-20261020.md)。Android新会话材料在 [handoff/README.md](../handoff/README.md)，仅源码和交接，不含生产配置/数据或已完成App。
