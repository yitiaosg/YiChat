# 生产迁移记录 · 2026-10-19

这是首轮迁移的历史快照。后续账户改进及用户授权启用YIDT功能见 [ACCOUNT-UPDATE-20261019.md](ACCOUNT-UPDATE-20261019.md)；当前接口和开关约定见 [CHAT-KERNEL-API.md](CHAT-KERNEL-API.md)。

实施分支 `refactor/yichat-server-v2`，生产目录 `/root/yitiao` 在 `deploy/yichat-server-v2`，由实施分支 fast-forward。基线 `baseline / 4aa436b` 保留；运行代码提交 `e591ca3`（主体 `df2ccd8`）。原工作区未提交改动通过在线快照、源码包和旧前端原目录迁移保护，没有清库或覆盖钱包密钥。

已执行 `deploy/setup.py`、重启 website.service、`manage status`、`manage reconcile`、`manage archive-once` 和 `deploy/backup.py`。website、yichat、yichat-synapse、yichat-archive 均 active/running，NRestarts=0；检查时三个 YiChat unit 的 ERROR journal 无记录。YIDT、parachute、webgame、Caddy 的 PID 与迁移前相同，Caddyfile 未修改。

只读核对：5 个原用户对应5个稳定 uid；2个原 Matrix username/MXID/加密凭证映射逐项相等；1个原房间保留；原文件数0。钱包数量及 yidt_transactions 末端哈希未变，reconcile 返回空问题列表。没有真实付款、领取、转账或赠卡试跑。

真实 Synapse 17个接受事件已采集，源水位18、healthy=true；2026-10-16、17、18 各两个规格共6份归档，6份实际ZIP逐一复算 SHA-256/大小与 PostgreSQL 相等；archive_gaps=0、failed_jobs=0。现有数据未到30天边界，生产未触发历史清理。30天/故障/崩溃/金融并发边界通过隔离测试验证，不能把它们称作已经运行了30天的生产结果。

HTTPS 基础检查14项：主站匿名账户302、静态社交脚本200，YIDT和跳伞200；聊天 landing/archives/health/discovery/Matrix versions200，OAuth login307，匿名 session401；旧 assets、web_a 和 Synapse admin 路由404。另验证公共资料和精确数量200、默认三种名单403。真实生产网关本机入口0.107秒内11次匿名 session 请求，前10次401、第11次429，M_LIMIT_EXCEEDED、Retry-After=1、retry_after_ms=899；公网往返耗时较长的请求不构成同一秒突发，不能拿其结果假装测过窗口边界。8018、5018、5432只监听本机，公开入口仍由 Caddy80/443提供TLS。生产账号登录/退出在临时真实主站 fixture 验证，没有冒用现有用户会话进行线上操作。

旧 `telegram-web-a`、`frontend`、`releases`（含原修改及 node_modules）、旧测试产物及前端专属许可清单整体移至 `/root/yitiao/backups/yichat-refactor-20261019T132742Z/retired-frontends/`，活动源码不再有它们。服务只提供最小入口、财务详情/明确操作和归档下载/校验页；未开发聊天UI或解密客户端。

备份：

- 修改前：`/root/yitiao/backups/yichat-refactor-20261019T132742Z/`，源码/配置包、四个SQLite快照、原密钥包、2167条前端未提交状态和部署前核验信息。
- 配置迁移前：`/root/yitiao/backups/yichat-config-v2-*`。
- 迁移后：`/root/yitiao/backups/yichat-v2-20261019T152439Z/`，四个SQLite在线快照、PG dump、原配置/密钥私密包及提交 manifest。

备份目录0700，数据库、配置、密钥包和 PG dump 为0600；未全局放宽目录权限。回退步骤和正文备份保留例外见 DEPLOYMENT.md，不能用旧账本覆盖之后的新交易。

生产资金功能明确关闭：`YICHAT_OFFICIAL_WALLET` 缺失，`YICHAT_FUNDS_ENABLED=false`。启用前需核实官方地址与原 yidt_core.OFFICIAL 一致并显式开启；没有补造托管地址或外部链配置。原 YIDT 是中心化哈希链账本，没有 RPC、区块确认深度或链重组协议，本实现复用其真实确认点；如果要外部链，需要另行实现链适配，不能宣称当前已支持外部链重组处理。

测试明细见 TEST-RESULTS.md，机器可读数据见 verification.json。凭据、真实用户名/MXID、账本哈希和私钥没有写入公开报告或Git。
