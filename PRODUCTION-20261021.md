# YiChat 0.3.0 / Android code 8

App 与网页继续共用 Preact / TypeScript、matrix-js-sdk 和 Rust WASM；Kotlin 只提供 WebView、系统浏览器授权、缓存和内部加密备份。包名及签名与 0.2.0 一致，直接覆盖安装，不清除数据。随机开发证书私钥保存在源码外，不进入仓库。

## 本次变更

- Android 使用 systemBars / displayCutout 安全区，键盘弹起取 IME 与导航栏的较大下边距；消费已处理的 Insets，避免双重位移。应用、自适应图标和网页品牌均使用用户提供的 android-icon-blue.png，网页圆角显示。
- 新群创建时登记永久随机九位群号。服务端登记的群主/管理员是管理权限依据；支持搜索开关、成员邀请开关、禁言（最多 24 小时）、移出、群主禁止重入、解除禁入、任命管理员、转让群主及五分钟内撤回。旧群迁移保留原群主并一次性读取原管理员。Matrix v12 的创建者原生特殊权限不改写，业务管理接口和原生事件入口均按当前登记权限审核。
- 私聊准备发送时由服务端让接收方加入；首次真实加密消息被接受前，对接收方隐藏空会话。发送与上传先检查主站隐私/拉黑；失败保留原事务重试和真实拒绝文案。自动加入仍使用原生 Matrix 加密和设备，不复用他人密钥。
- 聊天资料弹层复用主站 UID 对应的头像、介绍、关注/粉丝/朋友统计以及关注、拉黑接口。主站个人页按钮使用真实 SVG 图标和统一样式。
- 红包份数不能超过当前确认入群人数；创建和入金都再次校验。最低本金为份数 × 0.01；发送者支付 (份数 + 1) × 每笔 gas。领取按钮忙碌 500ms 后提交一次请求，真正的去重与资格仍由服务端事务保证。
- 已撤回加密消息的显示先读取认证的原始密文证明、验证 SHA-256，再让 SDK 使用历史会话密钥解密验证。没有历史密钥时显示恢复入口，不冒充已解密。App 将原始密文证明保存在自己的加密原包库；普通网页只保留内存缓存。
- 每日归档的 UTC 日期已写入规范化头和可信清单，空日也生成归档；本次保留现有封存规格，补充测试验证空日、不同日期哈希及撤回前原密文均被包含。Matrix 原生撤回元数据仍是协议控制事件，并未另造加密协议。
- 主站 YiChat 入口指向 https://chat.yitiao.org；普通网页设置提供 GitHub 最新 Android Release 下载。

## 新增接口

均沿用原有认证、SID、Origin、当前/历史房间权限与共享限速，不允许客户端自行宣称角色。

| 接口 | 用途 |
| --- | --- |
| GET /api/groups/{room} | 群号、当前角色、成员、禁言、禁入与邀请/搜索设置 |
| POST /api/groups/{room}/manage | action、稳定 key；settings/transfer/admin/mute/kick/unban/redact/name/topic；服务端复核权限 |
| GET /api/group-numbers/{number} | 登录用户查询允许搜索的九位群号 |
| POST /api/group-numbers/{number}/join | 稳定 key；按当前开关和禁入规则邀请并加入 |
| POST /api/rooms/{room}/prepare | 私聊发送前检查双方/隐私并自动加入接收方；不发送正文 |
| GET /api/people/{username} | 主站头像/介绍、统计、本人关注/拉黑状态 |
| POST /api/people/{username}/{action} | 复用主站 follow/unfollow/block/unblock 及原有规则 |
| POST /api/history/recall-ciphertexts | ids（最多 20）；仅返回认证撤回对应的原始加密事件、哈希或 available:false；不返回密钥/正文 |

群管理使用最多两分钟的服务器内部临时代理令牌，以及绑定 SID、操作者、房间、事件内容哈希的单次授权记录。令牌不交给客户端、不创建加密设备，完成后退出。旧群、旧设备及归档均不删除。

## 构建和部署

按 README.md 和 android/README.md 配置 Node、JDK 21、SDK 36 与源码外签名。`npm --prefix source/yichat/client ci` 后在 android 执行 `./scripts/build.sh`。Gradle 自动构建并嵌入同一网页产物。隔离仪器测试的 `-PyichatIsolation=true` 只允许 Debug 使用 localhost:18018；Release 始终使用真实 HTTPS 域名。

服务端以 source/yichat/backend 的增量文件更新原内核；SQLite migrate 只增添 group_* 和 direct_visibility 表。先保存数据库/源码私有快照，完成迁移并重启网关及 Synapse，再用现有 deploy/promote_web.py 原子切换网页。主站只部署对应模板、account-social CSS/JS 和入口文案，禁止用仓库脱敏的 source/website.py 覆盖完整生产主站。YIDT 资产内核和归档格式未替换。

## 回滚

修改前私有快照：/root/yitiao/backups/yichat-v2-20261021T005613Z；服务端基线 commit 4250084486e041ecc77f20775928475a04cd18d7；旧网页 /var/lib/yichat/web/releases/20261020T153651Z。保留新增数据库表及新生成群号，不恢复旧数据库覆盖业务数据。网关与 Synapse 权限模块应成对回滚；已有新管理权限记录必须先评估，不能在旧模块下放宽现有群权限。网页通过 current 软链原子切回旧 release。App 如需回退实现，应保持包名/签名并提高 versionCode，禁止卸载或清除密钥存储。

本次验证结果和截图在 delivery/production-20261021。资金压力测试全部使用隔离账本，不使用真实用户资金或卡片；离线缓存不是全量历史恢复，自动内部原包备份仍与 Matrix 密钥备份分别管理。

部署记录：最终网页 /var/lib/yichat/web/releases/20261021T021703Z；服务端最终提交 f7ca72aba88d79314412e7bb6a37c9cb76de331f。迁移完成后网关、Synapse、归档、主站均 active。补充更新前私有快照 /root/yitiao/backups/yichat-v2-20261021T021558Z，补充更新前提交 6f93f11898a4e8b4335036d0126d210c0827de49。

## 群头像、登录设备与 App 核验补充

群主可在会话详情选择、裁剪、保存或移除群头像。服务端在接收图片前检查当前群主/成员权限，解码并去除元数据，保存为 256×256 PNG；共用上传限速和并发预算。群成员通过认证接口读取当前头像。转让后立即按新群主管理，原生 Matrix avatar 事件也不能绕过权限。

聊天已移除设备 SAS 验证界面及请求；保留原生 Matrix 加密、独立密钥备份和恢复。主站 /account/ 列出当前及其他登录设备，可退出指定设备或其他所有设备。撤销的是该主站 SID 及关联 YiChat 会话，不删除加密设备、归档或本地密钥。在线 Chat 约 30 秒内检查，切回前台立即检查；离线设备在恢复网络后锁定。旧 SID 不伪造历史设备标签/活动时间。

App 设置及会话详情提供记录核验：连续 1–5 个 UTC 日期，可选择内部保存的原消息包或本地每日 ZIP 归档（events-manifest / with-attachments）。只上传会话、日期、数量/规格、大小和 SHA-256，不上传文件、正文或私钥。核验通过的日期才打开独立只读记录；私聊需要本地输入正确的 Matrix 恢复密钥，下载原生加密会话密钥备份并在本地解密。没有密钥或缺包时不伪造正文，不导入实时会话，不重新发送记录。

新增 `yichat-record-digests-v1` 是原消息包的日期绑定摘要，独立于原有两个 ZIP 规格，原封存文件字节完全不变。规范头 `{day,format,room_id,version:1}` 与逐条 `{event_id,received_ms,redacts,room_id,sender,sequence,sha256,type}` 按键排序、UTF-8 紧凑 JSON 加 LF，按 sequence/event_id 排序串联后 SHA-256。包含原始已撤回密文及撤回控制记录，空日也有头摘要。可信根从接受存证数据库生成并绑定 events-manifest 的封存 SHA；清理实体前保留。旧日已清理且未保存此新摘要时明确不可用，仍可用原每日 ZIP 摘要核验文件，不能补造存证。

本地文件入口读取服务器统一每日 ZIP；目前不把口令加密的 `.ycb` 外部快照当作每日统一归档。单个 ZIP 本地查看上限 100MB，事件文本 20MB；较大归档使用桌面工具。核验完整日仍需该日全程历史访问权限；当天尚未封存或入群不足完整日会明确提示。内部早期版本已删除的撤回原包无法补造，显示缺失/不一致。新版本保留撤回原包用于核验，但不会显示其正文或提供给对等恢复请求。

新增接口：

- `POST /api/groups/{room}/avatar`：群主上传标准化头像；`GET /api/groups/{room}/avatar/{id}`：当前成员认证读取；manage 增加 `avatar`（稳定 key，image ID 或 null）。
- `POST /api/records/verify`：`{room,start,end,days:[{day,count,sha256,complete}]}`，返回每日 ok/mismatch/incomplete/not_sealed/forbidden/quarantined/unavailable。
- `POST /api/records/verify-files`：`{room,start,end,days:[{day,spec,bytes,sha256}]}`，与两种原封存文件摘要比对，返回每日结果。
- `POST /account/devices/revoke/`：同源、CSRF、本人设备 ID，或 others；不接受原始 SID 作为客户端设备 ID。

部署补充：先增量部署新 backend 模块及 account_devices.py、模板/静态文件，SQLite migrate 新增头像、记录摘要表；再重启 yichat、yichat-synapse、yichat-archive、website 并原子 promote 网页。生产 website.py 只增加 include 与模块 install 四行，不覆盖脱敏源码。新增表保留，回滚时不要覆盖用户新消息或登录会话。
