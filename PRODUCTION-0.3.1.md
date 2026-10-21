# YiChat 0.3.1 / Android code 9

继续使用同一 Preact / TypeScript、matrix-js-sdk / Rust Crypto 前端，Kotlin 提供 WebView、系统浏览器授权、内部备份与系统通知。复用 0.3.0 的随机签名，包名 org.yitiao.yichat.mobile，可覆盖升级，不卸载、不清除密钥或归档。

## 本次行为

1. 私聊移除“离开会话”，网关和原生事件层均拒绝主动离开。群主可显式解散群；曾有至少两名成员的群在成员变更后只剩一人时自动解散。新建但邀请尚未接受的单人群不立即解散。解散不可撤销，不能发送、上传、邀请、修改群资料、入金或领取；原已确认的资金结算/到期退款照常执行。退出成员和已解散群仍可查看有历史权限的既往记录，刷新后保留只读会话，无输入框。消息、附件继续使用既有30日留存与存证清理，不立刻删除房间、密钥、原包或归档。
2. 最多8名管理员，在服务端 SQLite 写事务内检查；UI 达到上限后禁用新增。退出成员清除管理员标志；若升级前存在超限旧数据，按 MXID 排序保留前8名，群主可再调整。
3. 左滑归档、右滑置顶；重复操作取消。归档进入独立“归档”页签，与未读并列；右滑置顶未置顶的归档会话同时移出归档。桌面/键盘有等效按钮。按 UID 在服务器保存，账号隔离、刷新保留。设置保存有忙碌状态，失败回滚，旧请求不能覆盖新设置。
4. App 增加消息通知入口、系统权限申请和系统通知设置入口。会话详情可免打扰；归档/免打扰/退出/解散会话不通知。通知只显示“收到新的聊天消息”，不显示私聊正文或密钥，点击回到对应会话。前台由现有 SDK 的真实新事件通知；后台 WorkManager 每15分钟左右检查聚合接口，受系统电量、网络、后台限制影响，可进一步延迟；强制停止应用后不能接收。此版本没有 FCM/厂商推送连接，不承诺应用被杀后的即时推送。后台不另外调用 Matrix /sync，不争抢加密密钥或 to-device 队列；退出停止通知并撤销未读系统提示。
5. 撤回使用返回箭头 Undo2；私聊对方消息不显示撤回。群聊仅本人或当前有权限的群主可见，并保留5分钟和特殊消息不可撤回规则。服务端也禁止越权撤回私聊对方消息。
6. 群聊他人消息显示头像/昵称，同一天且相隔不足5分钟的连续同人消息只显示一次；手机也显示头像。私聊仅气泡，不显示对方头像/昵称行。
7. “我的交易详情”仅查询本人实际账本入账的发送转账、确认收款、发送红包、领取红包和退款，分页显示，点击对应真实 hash 的 YIDT 浏览器；不把授权、待入金订单当作已完成交易。金额使用整数/十进制字符串，无 Number 金额转换。
8. 主站实际过冷静期清理账户时，清空个人资料、头像、登录时间、社交关系和已登录设备信息，立即撤销主站 SID。Chat 进一步拒绝其登录、邀请和私聊发送，公开资料仅保留用户名，不暴露旧头像/简介。Synapse 每15秒做有界、可重试的官方 API 退群；群主注销时优先转给仍在群的管理员，否则按 MXID 选择一名有效成员，再退出；人数降至1自动解散。每轮每个注销用户最多处理10个房间，轮转避免失败房间阻塞其他房间。保留 UID/MXID、注销标记、存证和必要账本记录，以便旧聊天与资产记录正确匹配；不复用用户名。

## 新接口和数据

- `GET /api/rooms` 增加本人的已退出会话及 membership、dissolved_at、peer_deleted、revision。已退出不等于取得离开之后的消息权限；历史仍由原生 joined 可见性、接受存证和成员时间证明审核。
- `POST /api/groups/{room}/manage` 新增 `action:dissolve`，稳定 key 绑定操作，只有当前群主。GET 群详情增加 dissolved_at、admin_count、admin_limit:8、revision；退出者不获当前成员名单。
- `GET /api/conversations/preferences` 与 `POST /api/conversations/{room}/preferences`：本人 pinned/archived/muted 布尔值；已读 `{read:true,event:acceptedEventID}` 只推进到已接受消息的序号，不用客户端时间提前吞掉未到达的新消息。
- `GET /api/notifications?after=sequence`：每页聚合最多20个当前可通知会话，只返回房间、事件ID、序号和数量；has_more 表示继续从返回游标读取，App 每轮最多取两页，不跨过尚未通知的事件；不返回正文、密钥或附件；24小时范围、排除本人、已读、撤回、归档、静音、退出及已解散会话。无 after 仅返回当前游标，供只读基线使用。
- `GET /api/transactions?before=id`：本人已入账资产操作，每页30条，返回真实 hash、方向、金额、gas、时间及浏览器 URL。按订单/领取的稳定 UID 关联，不因共享钱包展示他人操作。

新增 gateway SQLite conversation_preferences；新增原有账本中的 yichat_group_lifecycle，成员离开/自动解散与资产权限共享同一事务锁。群主显式解散是单向状态，旧界面和原生事件也不能绕过。旧每日 ZIP/原包核验规格、密钥库、签名和资金幂等规则不变。

## 部署与回滚

先运行现有 deploy/backup.py 保存私有在线快照；保护原有未提交改动。增量部署 backend/client 与主站 purge_account 函数，禁止用脱敏 source/website.py 覆盖完整主站。使用原环境执行 db.migrate 和 Funds.migrate，建立新表后重启 yichat-synapse、yichat、yichat-archive、website，再用 deploy/promote_web.py 原子切换同一 dist。不需要更换域名、Caddy 路由、密钥、钱包或外部推送凭据。

Android 按 android/README.md 配置源码外签名，使用 JDK21 / SDK36 / Gradle8.13 构建。低资源环境使用 --no-daemon --max-workers=1 及较小 Gradle 堆。Gradle 自动嵌入同一份网页产物；签名私钥不进入源码包或 GitHub。

上一版本服务器基线 f7ca72aba88d79314412e7bb6a37c9cb76de331f，网页 /var/lib/yichat/web/releases/20261021T021703Z。保留新增表及已经生效的解散/注销标记；不要用旧数据库覆盖业务记录。回滚网页可原子切回旧静态目录；后端若回退实现，必须保留解散和注销拦截，不能让终止群重新开放。App 回退需同包名/签名并提高 versionCode，禁止卸载清除密钥。

本次按用户要求只做浏览器实测与构建检查，不启动 Android 模拟器；系统通知呈现、点击返回、各厂商后台调度由用户真机验收。截图和实际结果见 delivery/production-20261021-v031。

部署记录：服务器最终提交 b59f1024155f3f11f83e3f779a962882bae3cdd3；网页 /var/lib/yichat/web/releases/20261021T075026Z；更新前私有快照 /root/yitiao/backups/yichat-v2-20261021T072223Z。后续通知分页修订仅调整聚合游标，不触及消息、密钥、金额或归档规格。

共享网页读取 App 原生 session.capabilities.notifications：旧APK缺少该能力时不展示通知入口或调用通知桥；备份和现有聊天照常可用。通知分页专项测试显式提高隔离 Synapse 的建群速率以准备21个真实房间，线上和测试网关的10次/秒共享限制均保留。
