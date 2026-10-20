# YiChat Web 验收记录（2026-10-19 UTC）

## 验证范围

在独立工作树 `feat/yichat-web` 开发；生产基线 `31b9070`。测试启动临时 Synapse、原有模块与网关、原 OIDC 提供者及 YIDT 服务，使用隔离 SQLite、专用 PostgreSQL schema 和 Chromium。没有使用真实用户资金、卡片或生产聊天账号。

| 检查 | 实际结果 |
| --- | --- |
| TypeScript 严格检查与 Vite 生产构建 | 通过，包含原生 Rust Crypto WASM |
| YiChat 全量 pytest | 89 passed，1 项原有 Starlette/httpx 弃用警告 |
| 聊天真实浏览器长流程 | 27 项通过，0 个未捕获脚本异常 |
| 精确金额、Unicode、URL 白名单与 Retry-After | 4 项通过 |
| 主站社交页面 unittest | 6 项通过 |
| 原 YIDT unittest | 45 项通过 |
| 主站账户真实浏览器流程 | 16 项通过，0 个脚本异常 |
| Caddy 配置验证 | 通过，原有排版提示，不修改配置 |

浏览器流程的 27 项检查覆盖：两账号新私聊/邀请、Megolm 密文与新群明文、刷新不丢密钥、回复/编辑/撤回、历史和已读、三账号邀请/管理/本人离群、跨标签加密库接管、数字 SAS 验证、原生密钥备份及第二设备恢复、隐私原文失败提示与原 txn 重试、429 等待和发送响应丢失去重、创建响应丢失核实、手机导航/主题/无横向溢出、双方游戏场景和 YCard 实物自动渲染、私聊附件密文上传/鉴权下载、图片/录音/视频、订单创建无自动扣款、原 YIDT 授权页明确批准后 fund、群红包 claim、私聊 receive、重复资产请求幂等、真实赠卡后独立消息、归档 UTC 与完整日权限、退出撤销与真实 OAuth 回登。

浏览器特意注入 429、响应丢失及附件 410/503；状态机和实际后端相应测试一起核验。410 注入不是生产文件自然到期。网络中的未建立备份 404、注入 429/410/503 和权限拒绝 403 是预期结果；统计的零异常指没有未捕获 JavaScript 异常，不代表所有网络响应都是 2xx。

资源回收改动后再次运行严格构建及完整聊天浏览器长流程。测试脚本的丢失创建响应模拟增加完成屏障，防止撤销拦截早于注入结束；没有放宽产品断言。

## 联调发现并修复

- Synapse 的重复资料 provision 会产生同 membership 候选事件，去重后不触发持久化回调；原成员资金屏障因而永久 pending。仅实际 membership 变化安装屏障，资料更新不覆盖其他成员变更，真实 Synapse 回归通过。
- 房间 v12 创建者隐式最高权限由原 SDK/Synapse 处理，避免旧式本地权限计算拒绝合法邀请。
- 原 YIDT CSP 的 form-action 没有聊天域名，真实授权表单提交后返回被浏览器拦截；仅精确加入 `https://chat.yitiao.org`。
- 解密附件权限证据短暂落后时，只对带 `YICHAT_HISTORY_PENDING` 的 503 按服务端等待时间有限重试；真正依赖故障和到期分开呈现。

## 截图

本地 `yichat/tests/browser/artifacts/` 保存 `chat-desktop-group.png`、`chat-desktop-private.png`、`chat-receiver-private.png`、`chat-mobile.png`、`chat-mobile-list.png` 和 `chat-order-confirm.png`。图中账号与资产均是隔离测试数据；分享画面由原渲染器和真实测试实例结构生成，产品没有演示数据分支。截图/运行密钥/临时账本不提交 Git。

## 明确限制

- 已实测 Chromium 桌面与手机视口；没有完成 Safari、Firefox 或实体 iOS/Android 验证。录音/视频媒体测试使用 Chromium 虚拟设备流。
- 未在浏览器完成一次 100MB 成功上传，也未做超大群或十万条历史压力测试；后端已有实际字节上限、限速和并发测试。时间线分批展开并使用 content-visibility，重型分享仅在可视时挂载，缓存限制24MiB/160项；不是完整可变高度回收列表。
- 新归档弹窗已实测 UTC 日期、权限拒绝及临时快照拒绝；完整封存文件/可信摘要/本地 SHA-256 正反例由既有归档浏览器与后端测试覆盖，没有把新会话强行伪造为拥有完整日权限。
- 大归档 SHA-256 当前在浏览器本地内存计算，未做超大文件内存压力测试。实时消息不标记为每日归档验证通过。
- 线上仅执行匿名/只读检查；完整登录、双账号加密和资产流程在隔离服务实测，未向真实用户发送测试消息。

## 生产发布记录

- 生产源码提交：`92a103c27ef3d743142e97e2137c511cd9ee11bb`，由 `31b9070` 干净工作树 fast-forward；随后仅补交此验收记录。
- 静态 release：`/var/lib/yichat/web/releases/20261019T180158Z`；默认 `yichat/client/dist` 经 `current` 原子链接指向此版本。线上 JS/CSS/WASM 与测试构建逐字节核对一致，WASM MIME 为 application/wasm。
- 私有部署前备份：`/root/yitiao/backups/yichat-v2-20261019T180007Z/`（另有本轮开发开始时备份）。11 个原配置/密钥文件与备份逐一 SHA-256 比对，未改变；不在公开文档记录摘要或密钥。
- `website`、`yidt`、`yichat`、`yichat-synapse`、`yichat-archive`、`caddy` 均 active；发布后 warning 级服务 journal 无条目。Caddy 配置未改。
- HTTPS `/health` 为 200/ok、funds_enabled=true；管理 reconcile 返回 `[]`。原归档 checkpoint 健康、缺口0，封存规格数6，未覆盖任何归档。
- 生产公共社交 API 已核验 `chat_available=true`；线上 account-social.js 使用 `?to=<username>` 进入聊天。
- 真实 HTTPS Chromium 桌面/390px手机页面为200、无横向溢出，实际点击进入原 `yitiao.org/login/` 用户名登录页。没有以真实用户登录或发送消息/资产。
- 线上未捕获脚本异常0。明确保留的控制台记录：匿名 `/api/session` 的预期401及锁定取消；Cloudflare 在边缘注入的 beacon 与内联挑战脚本被同源 CSP 拦截。没有放宽 CSP；应用脚本正常加载。Python urllib 默认 UA 曾被边缘403，curl 与实际 Chromium 请求通过。
- 网关 render 缓存设1小时；现有 Cloudflare 对外观察为4小时，哈希 assets 为一年 immutable、HTML/API no-store。后续修改非哈希 render 资源应同时考虑现有边缘缓存刷新策略。
- 本次额外保存线上截图 `live-chat-desktop.png`、`live-chat-mobile.png`、`live-oauth-login.png` 和 `live-results.json`，均位于浏览器 artifacts 目录。

完整操作与回滚步骤见 `WEB-DEPLOYMENT.md`。


## 2026-10-20 增量验收

本轮新增主页真实钱包绑定、本地时区、5分钟撤回/业务消息保护、增量事件包与在线成员补齐、赠卡快照及主站头像/简介。最终98项回归通过（全量96项，加修正测试辅助代码后的定向复验2项），聊天浏览器32项通过/未捕获异常0，前端Node5项通过，严格构建通过。具体方法、模拟清理范围、截图和边界见 `WEB-UPDATES-20261020.md`；本节不把模拟32日清理称作生产自然到期。

## 转账气泡与 App 范围调整（2026-10-20）

最新完整回归100项通过（210.72秒），其中真实浏览器32项通过、未捕获JS异常0；前端单测5项及生产构建通过。含两种资金响应丢失后的单次收款/单条回执验证。详细范围、截图、上线与回滚见 [WEB-MONEY-BUBBLES-20261020.md](WEB-MONEY-BUBBLES-20261020.md)。Web 已停止成员数据包备份/恢复，相关旧版本描述不代表当前行为。
