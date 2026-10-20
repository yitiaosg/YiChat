# 部署与回退

## 已检查的原部署

项目 `/root/yitiao/`，Debian 13+Caddy。主站 website.service 在 127.0.0.1:5001，YIDT 在 5010，跳伞在 5008；YiChat 网关 5018、Synapse 8018 均本机监听。Caddy 既有主站/YIDT/游戏路由保留，chat.yitiao.org 只转发网关，不公开 Synapse/admin/media/object。

原根目录没有 Git。实施前建立源码基线 `4aa436b` 和独立分支 `refactor/yichat-server-v2`，隔离工作树 `/home/claude/yichat-work`。Telegram 子仓库原有 2167 条未提交状态已单独记录，其源码、未跟踪文件和 Git 元数据保存在原始备份；node_modules 可由原锁文件重建。没有将源码变更误当成干净上游覆盖。

初始备份：`/root/yitiao/backups/yichat-refactor-20261019T132742Z/`，包含修改前源码/配置包、四个 SQLite 在线快照及原前端状态。钱包/站点/OIDC/Synapse 原密钥另由 v2 私密备份保存。所有备份保持仅 root 可访问，绝不放进 Git 或网页。

## 更新已有部署

这是一套已有服务的升级，必须保留原密钥、真实数据库和钱包绑定。安装缺失的 PostgreSQL（本机监听）后：

```bash
cd /root/yitiao/yichat
uv pip sync --python .venv/bin/python requirements.lock
sudo .venv/bin/python deploy/backup.py  # 升级后/日常备份；首次升级另用已有在线备份
sudo .venv/bin/python deploy/setup.py
sudo systemctl restart website.service
sudo .venv/bin/python -m backend.manage status
sudo .venv/bin/python -m backend.manage reconcile
sudo journalctl -u yichat -u yichat-synapse -u yichat-archive --since '10 minutes ago'
```

首次升级在新 PG 库尚不存在时先保留已有 SQLite/配置/密钥在线备份，再运行 setup；backup.py 需要 v2 已建库。恢复既有环境时从备份取原 YICHAT_SESSION_KEY、管理 token 和 Synapse signing key，安装器不编造或轮换它们。

setup 幂等创建独立 `yichat_archive` PostgreSQL 库及 yc_owner/yc_ingest/yc_seal/yc_read/yc_cleanup 角色，随机密码只存 `/etc/yichat/archive-dsns.json`（0600）。owner 不进运行服务；API/模块只载入 read/ingest，独立 archive-worker.env 才有 seal/cleanup。SQL 的权限和不可变触发器保护封存摘要，原部署 root 服务身份保持不变，不通过放宽全局权限解决访问问题。

安装器停止 YiChat API/归档 worker，保留现有其他网站进程；增加主站 account_ids/关系/隐私表、网关 v2 表、财务参与者 uid/唯一约束/成员屏障和独立 PG 存证表。旧 MXID、用户、设备密钥、房间、业务/资金账本不删除。旧房间真正登记为 direct 或 legacy_encrypted，附件改为统一 UTC 日结束+30天；旧文件有实体则按实际字节补 SHA，无实体则登记缺口，不能伪造完整摘要。

Synapse 模块通过现有 EnvironmentFile 和 PYTHONPATH 装载，读权限/房间注册记录，用回调执行准入和持久采集。独立 worker 处理封存、源 purge API 任务、证据审计与清理。配置保留本机监听、关闭公开注册/联邦/原生媒体，retention 自动删除关闭，防止在可靠存证之前独立删除。

主站必须重启以加载新菜单、uid claim 和共享社交限流；已有 YIDT 注册保持生效，无需改其余额或重启游戏。先看 health/source checkpoint、archive_jobs、archive_gaps；错误时暂停清理并产生 ERROR journal，health 返回 degraded。只读核验 TLS、域名、登录跳转、静态旧路由 404 和端口监听。

## 必填/保留配置

参考 deploy/yichat.env.example。必需：原 session key、原 Synapse admin token、真实主站/account/ledger 路径、现有 Synapse 地址、生成后的 read/ingest DSN，worker 的 seal/cleanup DSN。`/etc/yichat`、备份与密钥不可公开。

资金仍由 `YICHAT_FUNDS_ENABLED` 和实际官方托管地址共同控制，必须与 yidt_core.OFFICIAL 一致。缺配置返回 503，剩余模块照常完成。当前 YIDT 不需要额外链 RPC 或资金私钥；现有 .yidt_key 用于原钱包秘密保护，不能丢失或放入前端/日志/Git。不得通过真实资产验证部署。

上传固定单文件 100000000 字节、聚合请求固定 10/滚动秒；上传速率本部署 500000 字节/秒、3 个并发、默认日额度 1GiB。两个资金 TTL 默认 86400 秒，可调整为正整数。

## 旧前端退役与已有加密群

Telegram、旧 React 聊天前端及 releases 从活动 yichat 目录整体移到私有备份，专属构建/资源/测试路由移除。原修改与源许可证留在私密备份供回退审核，不公开继续提供旧聊天 UI。`/sw.js` 只提供退役 worker：删除已核实的 `yichat-shell-*` 静态缓存、注销自身并刷新窗口；不删除 IndexedDB、本地私钥或用户归档。

旧加密群不能通过删除 m.room.encryption 关闭加密。迁移方式：由原群主用 `/api/rooms` 创建新的 plain 群；按原群当前真实成员重新邀请，明确告知旧群/新群 ID 与切换时间。旧群保持加密、历史、设备验证与用户加密备份，旧事件按原 receive day 存证；新群的新事件服务器可读并经 HTTPS。不得拷贝解密正文到新群或自动重发历史。当前未开发聊天 UI/迁移 UI，API 与此操作规范供未来客户端使用。

## 回退和灾难恢复

出现异常先将 chat.yitiao.org 撤到维护态：停止 yichat.service、yichat-archive.service；Synapse 保持仅本机，主站/YIDT/游戏继续可用。不要回到会绕过当前隐私/存证规则的旧公共聊天 UI。原代码和配置已在上述备份及 baseline 中，可在隔离 worktree 审核恢复；只回退主站页面时保留 uid 表/触发器和新财务/存证数据。

迁移是追加式，不提供 drop table 回滚，不清库。财务开始产生新交易后不能恢复旧 userdata.db 覆盖新账本；不要独立恢复其 outbox。需要全局灾难恢复时，暂停所有相关写入，按同一维护点恢复 account/chat/Synapse/ledger 与原密钥及 PG 存证，核实账本链、最大已接受 sequence 和当前源水位再开放。在线备份是各 DB 各自的完整快照，不能谎称它们是跨服务原子快照。

模块启动先恢复源和成员屏障，恢复完成前拒绝新写入。源 DB 如果落后于已接受存证，会 fail-closed，防止重用已接受顺序。SQLite 重启可复用从未接受的尾部空号，采集读取“最后实际事件”，而不拿包含空号的旧水位跳过新事件；仅相应未完成 fence 可安全收缩，已封存摘要不变。

备份保留策略：金融/资产账本、摘要和必要权限证据长期保存；正文、附件、归档实体、Synapse 内容快照按 30 天规则处理。升级前恢复包是明确记录的备份例外，应置于受控的限期保留计划，不宣称它已消除所有副本。后续运行备份不要重新引入长期正文/附件副本。
