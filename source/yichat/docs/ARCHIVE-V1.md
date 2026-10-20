# YiChat Archive v1

## 数据来源与封存

只采集 Synapse 实际持久接受且非 outlier、无 rejection_reason 的事件。接收时间来自 `events.received_ts`，稳定顺序来自接收持久化路径分配的 `stream_ordering`，不使用客户端 origin_server_ts 定日，不导出动态 /sync。`event_json` 只读适配器固定 Synapse 1.163.0；服务不写 Synapse 内部表。

PG events 以 event_id 主键、sequence 唯一，事件、成员历史证据和采集游标在同一事务提交。重复采集去重，内容不一致则停止。on_new_event 是通知加速，**不是可靠性的唯一来源**；后台以持久 cursor 从 Synapse 重扫，修复持久化后进程崩溃/回调失败的空隙。

UTC 日期范围 `[00:00:00.000,次日00:00:00.000)`。午夜后捕获全局已分配流 ID fence，等待所有 event writer 的连续持久水位达到 fence，才允许封存。这包括较早分配但尚未完成的写事务，不能用 MAX(event_id) 代替。多个采集/封存实例在 PG advisory transaction lock 下串行提交并通过唯一键去重。读取游标使用最后实际接受事件，完成水位独立用于 fence；SQLite 重启的未使用尾部空号不会使新事件被跳过。源时钟回退、采集失败、晚到已封存日期事件都会阻止清理并告警。

上传开始先写独立 PG reservation，完成后写实际字节哈希 receipt；跨日上传或崩溃未补齐 receipt 时阻止封存。失联任务只在租约结束、SQLite receipt 已补采且确认文件状态后回收。历史文件缺失、缺可靠字节哈希会形成 archive_gaps，不能冒充完整归档。

当天仅提供明确 provisional 的快照；封存后的编辑和撤回是新事件，进入其**实际接收日**，不改旧文件。格式升级需另立版本。

## 字节格式

版本为整数 1。JSON 使用 UTF-8 无 BOM，键按 Unicode 码点升序，紧凑分隔符 `,`/`:`，非 ASCII 字符不转义，不接受 NaN/Infinity，每个 JSON 记录以单个 LF (`0A`) 结束；无 CRLF/缩进/末尾空格。参考实现 `backend/archive.py:canonical`，固定 Python 3.11 的 JSON 序列化，样例为互操作测试向量。事件使用源持久 JSON，去除动态 unsigned，保留 content、必要加密字段、hashes/signatures、引用、拓扑等源字段，并固定补齐 event_id/room_id。

ZIP 规格分别为 **events-manifest**、**with-attachments**，摘要不能混用。两者都包含：

| 路径 | 固定内容 |
|---|---|
| `archive.json` | day、format=`yichat-archive`、permission_spec=`joined-whole-utc-day-v1`、room_id、spec、version |
| `events.ndjson` | 按 `(sequence,event_id)` 排序；每行 event、event_id、received_ms、room_id、sender、sequence |
| `attachments.json` | attachments 按 id 排序，字段 id/bytes/sha256/received_ms/uploader/event_ids；event_ids 字典序，另含 day、room_id、version |

with-attachments 另有 `attachments/{id}` 的实际上传字节。群附件哈希原始明文字节，私聊哈希上传的密文字节，服务器不解密或接受客户端自报哈希。受鉴权上传建立实际字节 receipt；发送者在事件外层 content 的 `org.yitiao.attachments` 声明 id，模块核实同一上传者和会话，持久采集时将其与实际接受的 event_id、接收日期原子关联。无需从 E2EE 正文提取地址。同一附件跨日引用时，各引用日独立包含附件和当天 event_ids。清单不包含尚未发送的暂存附件，未发送附件仅上传者可取。

归档文件无顶层随机目录、下载者、设备、动态导出时间或恢复密钥。ZIP 使用 ZIP_STORED、不压缩；ASCII 路径字典序、无目录条目、DOS 时间固定 1980-01-01 00:00:00、create_system=3、外部文件权限 0100600、空 comment/extra，所有条目采用固定 ZIP64 流式写法。文件顺序和 ZIP 元数据由参考实现固定。缺失、大小不符或 SHA 不符的附件禁止生成完整 with-attachments 规格。

落盘先写临时文件、fsync、rename、fsync 目录，再提交不可变 PG 摘要。数据库提交前崩溃留下未发布文件，可按同一源数据确定性重建；没有可信摘要的文件不供下载。下载前再次校验文件 SHA/大小。重复及多人下载取同一完整文件，字节一致。

## 长期存证和权限

独立 PostgreSQL `yichat` schema：archives 唯一 `(room,day,version,spec)`，记录 SHA-256、字节/事件数、顺序范围、sealed_at/state、附件清单摘要、permission_spec、fence_sequence。memberships 长期保留必要历史权限证据；archive_audit / archive_jobs 独立记录封存、失败、清理开始/结束、Synapse task 状态。archives 禁止 UPDATE/DELETE 的触发器进一步保护封存摘要；普通 API role 只有读取，采集 role 不能写摘要，封存 role 不能改摘要。

下载、摘要、附件每次认证。整日文件要求成员在日期开始时已 join，且整日无 invite/leave/ban 等权限缺口。后来加入、退出再加入不能得到完整日；当前仍是成员也不能补齐旧日。joined 可见性由模块固定。首次建群当天未覆盖完整 UTC 日的成员也会被保守拒绝。当前版本不提供部分导出；将来部分导出必须使用独立范围、标识和摘要，不能冒充完整日。

- GET `/api/archives/{room}/{YYYY-MM-DD}/summary?spec=events-manifest|with-attachments`
- GET 同路径 `/download`：未封存 409；实体已清理 410；历史范围不足 403 `YICHAT_PARTIAL_HISTORY`
- GET 同路径 `/snapshot`：仅 UTC 当天，独立 `snapshot-events-only-v1`，state=provisional、complete_day=false，不等于整日封存摘要
- 网页入口 `https://chat.yitiao.org/archives/`

## 30 天清理

私聊和群聊采用同一30日规则，所有自然日及到期边界均为GMT+0（UTC），与客户端或服务器显示时区无关。资金订单24小时收款/领取时限独立于聊天历史保留期。

统一边界为该 UTC **日结束 + 30×86400 秒**。例如 2026-01-02 的内容从 2026-02-02 00:00:00 UTC 起具备清理资格；此策略按整日归档计算，取代私聊 7 天和群自定义 1～30 天。到期不代表可以无条件删除：两个完整规格必须均封存、摘要可靠提交、实体复核一致、采集健康、源历史 purge 完成，才删除自定义事件实体、ZIP 与附件。

Synapse purge 只经管理 API 提交持久任务。边界选择依据接收时间验证过的拓扑前缀，不信任 origin_server_ts；前缀任何一天缺存证/未到期就暂停。失败及未知任务状态不冒认完成。必要当前状态、前向极值、加密状态、成员权限证据保留，避免破坏房间运行；无法安全清理的拓扑前缀会多保留并报告。对象删除开始前先持久化已验证清理意图，崩溃后幂等补完，不重造已不存在的下载实体。

长期保留 summaries/spec/权限与审计证据、YIDT/资金/资产账本；普通聊天撤回和清理不碰这些记录。媒体仓库已关闭，无 Synapse 缩略图或远程媒体缓存。新部署对象路径是本地无版本目录；若增加对象版本、缓存、CDN、快照或备份，运营方必须同步落实生命周期，不能仅删除当前对象就声称全部删除。

共享附件实体至少保留至最后一次实际接受引用的 UTC 日结束+30天；清理早期引用日不会提前删除后续日期需要的实体。准入到持久化期间有独立 admission，崩溃恢复前禁止删除对应实体。小体积上传/引用/哈希权限证据长期保留。未发送暂存对象不属于会话历史：只在其上传日结束+30天、源采集健康且无引用和未决 admission 时回收；不将草稿发布给成员来凑完整归档。

生产升级前备份包括历史 Synapse 数据，属于明确的恢复例外。运行内容/archives 不应进入长期备份；源 Synapse DB 的备份需相同 30 天生命周期，必要金融/权限/摘要备份独立长期保存。应用不能宣称清除了既有全盘快照、用户设备副本或用户下载。

## 本地验证与私聊恢复边界

网页和 `tools/verify_archive.py` 从认证服务器获取对应 room/day/spec/version 的可信摘要，再在本机计算文件 SHA-256 和字节数。只发送摘要 GET，不上传文件、私聊正文或密钥。实体清理后摘要仍可查询，但不能伪造下载。

SHA-256 证明文件与该服务器存证一致，不证明服务器不可作恶，也不构成第三方不可抵赖。群聊未来可从已校验事件展示；私聊未来只能展示已校验密文**实际解密得到**的内容，不能信另附的明文。密钥独立位于用户加密备份，不进入统一归档；恢复需要文件和对应密钥。未来 App 多日分别验证，再按 event_id 去重合并。本次只提供下载和本地哈希验证，没有网页恢复或聊天重发。
