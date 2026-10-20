# 会话资金气泡与 Web/App 边界调整

转账与群红包现在直接显示在消息流中。点击气泡展开权威订单金额、双方、费用、24小时期限、领取记录、退款和可执行操作。历史纯订单 URL 也可显示同样的气泡。独立的旧订单详情页已改为跳回聊天；不会因访问链接、预览、刷新或授权返回执行入金。

YIDT 授权使用当前标签页，完成后进入原会话内的确认弹窗。用户明确确认入金后，客户端以原订单和独立稳定消息 txn 发送转账/红包气泡，无需再手动发送订单链接。授权未批准时原 fund API 仍拒绝；返回地址本身不能证明批准。

私聊指定接收者明确确认收款后，客户端向原私聊发送“已收款”回执。回执使用原生 E2EE 和独立消息 txn；服务端实际 claims 及冻结接收人 UID 对应的 MXID 决定其可信显示。到账排队时显示“到账处理中”，真实 paid 后显示“已到账”。响应丢失先查询原订单，回执失败只重试消息，不再次执行资产操作。新回执使用与原订单相同的外层业务引用，禁止撤回。

Web 只保留实时/服务器历史聊天、撤回元数据同步及必要的加密本地设备存储、待发箱和业务幂等记录。不再自动保存、请求或提供历史数据包，不调用 history/packets 或 history/exchange。已存在的缓存及设备密钥不删除。服务器撤回同步仍正常重叠5分钟，重新进入重叠10分钟。手动归档下载/校验及用户主动选择的 Matrix 密钥备份继续可用；后者不备份消息正文。

App 尚未实现。自动备份、从在线 App 成员恢复消息、恢复任务持久化、容量策略和跨设备全局消息去重需要在 App 阶段实现和验收。内核认证包接口保留；服务器仍有的数据必须直接由服务器提供，仅明确清理后的缺失数据可向有历史权限的其他用户请求，私聊仅原生密文并校验原服务器摘要。Web 本地幂等记录不构成未来 App 跨设备全局回执去重保证；资金幂等继续由原权威账本保证。

加密发送兼容：每个页面首次向某个加密会话发送前，读取原生 Matrix 当前成员事件（不使用较早 sync token 的成员快照），交由 SDK 更新本地成员状态并获取设备列表，再由 Rust Crypto 分发会话密钥。保留成员懒加载，不删除或重建原设备/历史密钥，不以额外轮换密钥掩盖不完整名单。这覆盖了从 YIDT 返回新页面后接收方缺少会话密钥的回归。

## 部署与回滚

本次无数据库迁移，无主站、YIDT、Caddy 或 Synapse 策略变更。构建流程沿用 WEB-DEPLOYMENT.md，源码 fast-forward 后使用 deploy/promote_web.py 原子发布静态资源，只需重启 yichat 网关。原归档、资金队列和密钥不改写。

发布前源码为 b268344，静态版本为 /var/lib/yichat/web/releases/20261020T013643Z。发布前在线备份：/root/yitiao/backups/yichat-v2-20261020T031021Z/。需要回滚时先确认工作树干净并备份，再切换至 b268344 的独立 rollback 分支，并将 /var/lib/yichat/web/current 原子切回上述静态版本，重启 yichat。不要将旧数据库覆盖现有消息、资金或归档，不删除 IndexedDB。回滚会恢复旧版 Web 成员间数据包补齐行为。

## 验证

- 完整 pytest：100 passed，210.72秒。4条现有依赖弃用提示，无测试失败。
- 其中真实 Chromium 浏览器32项检查通过，未捕获 JS 异常0；真实原生 Rust WASM、隔离 Synapse、原 OIDC/YIDT 流程。桌面1440及手机390宽度实际点击；并非模拟聊天接口。
- 资金验证只使用隔离账本，不使用生产用户资金或卡片。覆盖未批准 fund 拒绝、授权返回没有入金、确认后自动发送气泡、指定人主动 receive、红包 claim、重复 fund/receive/claim 幂等；同时丢失 receive 响应和回执发送响应，查询/重试后只有一笔收款、一条回执。
- 两账号私聊 E2EE、明文群聊、两设备密钥备份恢复/SAS、刷新及跨授权页面返回均通过；主站头像在返回后仍实际加载。
- Web 无 packets/exchange 自动请求，IndexedDB 无新增历史包；服务端浏览器传输拒绝及原生客户端权限/哈希/服务器优先规则均通过。
- 邀请、历史、回复/编辑/撤回、离线撤回10分钟重叠、隐私拒绝、加密文件/音视频、410/503、429退避、游戏/YCard双方预渲染、赠卡、归档校验权限、退出及真实 OAuth 全部回归通过。
- npm test：5 passed。严格 TypeScript检查及 Vite生产构建通过。

截图在 tests/browser/artifacts：chat-transfer-receipt.png、chat-mobile-transfer.png、chat-packet-bubble.png、chat-authorization-return.png；机器结果 chat-results.json。截图均使用隔离数据。

## 生产发布记录

- 已发布到 https://chat.yitiao.org/ 。实现提交 b067182ec70a10804ebbb2866ccab831599f2a7a；静态 release /var/lib/yichat/web/releases/20261020T031315Z。
- 发布前备份 /root/yitiao/backups/yichat-v2-20261020T031021Z/，以 fast-forward 部署，只重启 yichat。6个服务 active，health=ok；现有订单全部对账通过；归档入口健康、sequence=35、gaps=0、sealed specifications=8。
- 11个配置及密钥文件与备份字节一致。原数据库、密钥和归档没有回滚或重建；无 schema 迁移。
- 线上桌面1440/手机390实际打开及点击登录，均进入主站 /login/；无横向溢出、未捕获JS异常0；旧订单地址303直接进入聊天，未发起资金动作。线上截图 live-money-desktop.png / live-money-mobile.png，结果 live-money-results.json。
- 入口的本方资产引用与 release 一致，JS/CSS逐字节一致，WASM GET 的 MIME为 application/wasm；静态 immutable，HTML/API no-store。检查采用 GET；Cloudflare可能追加页面脚本，CSP未放宽。/.env 与 /__test/control 均404。发布后网关 warning journal 无条目。
- 尚未验收真实 iOS/Android 硬件及 Safari/Firefox；本次实际浏览器为 Chromium。原生 App 的自动备份、恢复任务和跨设备回执去重仍属后续工作。


历史 packets/exchange 的原生传输约束：必须携带 Bearer 网关 token，且不能包含浏览器 Origin 或 Sec-Fetch-Mode；旧 Web 标签页亦返回 403/YICHAT_APP_ONLY。updates 仍允许 Web 核对撤回元数据。这是客户端范围约束，不是 App 身份证明，所有原权限、可信摘要及服务器优先规则继续执行。
