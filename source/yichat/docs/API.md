# 接口与权限契约

完整字段、调用流程、错误、资金和归档示例见 [CHAT-KERNEL-API.md](CHAT-KERNEL-API.md)。私聊及群聊统一按GMT+0日结束+30天到期；本轮已授权启用生产YIDT功能，实际开关以 `/health` 与 `/api/session` 为准。

## 身份和认证

`account.db.account_ids.uid` 是随机生成、持久化且不可修改的 32 位十六进制用户 ID。新用户由主站建号触发器分配，用户名变更只更新 lookup。YiChat OIDC 的 `sub=user_id`，签名仍由原主站提供；YIDT 和其他原有 RP 的 `sub` 不改变。OIDC 校验 RS256 签名、issuer、audience、exp/iat、一次性 state/nonce、S256 PKCE 和真实主站 sid。用户名由签名中的 preferred_username 与账号库绑定检查。

旧 YiChat identity 在迁移时绑定 uid，**原 MXID 不变**，设备、交叉签名、密钥备份、房间与用户数据保留；新账号 MXID 为 `@u_<sha256(uid)>:chat.yitiao.org`。每个 uid/MXID 都有唯一约束。所有客户端只能通过公共网关；原生上游 token 不发给客户端。

`GET /auth/login` → 主站授权 → `/auth/callback` → 安全 HttpOnly YiChat cookie。`GET /api/session` 提供 user、mxid、device、token、期限与上传约束，未来 SDK 使用此不透明 token 作为 Matrix access token，base URL 为 `https://chat.yitiao.org`。原生 Matrix token 使用原有 Fernet 配置加密存储。

认证请求每次检查 `yidt_sessions`。主站 `/logout/` 撤销当前浏览器 sid、对应 OAuth codes/tokens；网关立即拒绝所有关联设备请求，长轮询返回前复查。Synapse 模块每秒撤销关联原生 token，失败持久重试；使用固定版本 auth handler 的 token-only 方法，保留设备密钥，不调用会删设备的 Matrix `/logout`。账号安全操作原有全账号撤销继续适用。业务/权限服务故障返回 503，绝不放行。

Cookie 写请求要求同源 Origin；原生 App 可以使用 Bearer、不发送 Origin。未开放跨域 CORS。主站关系/隐私写请求另需 `X-CSRF-Token`（页面 meta）或表单 csrf；不接受 GET 变更。

## 关系、页面与隐私

主站 API：

| 方法/路径 | 契约 |
|---|---|
| GET `/api/social/{uid}` | 精确 counts、lists_visible、following、mutual、blocked、is_self、can_message；chat_available=false |
| POST `/api/social/{uid}/{follow,unfollow,block,unblock}` | 认证+CSRF；事务、唯一约束、幂等；禁止自身关系 |
| GET `/api/social/{uid}/lists/{following,followers,friends,blocked}?cursor={uid}` | 每页 30、next_cursor；按不可变 uid keyset 排序；每次重新校验名单权限 |
| POST `/api/social/block-username` | `{username}`，只将用户名解析到 uid，随后按 uid 拉黑 |
| GET/POST `/account/privacy/` | following/followers/friends 分别可见，默认 false；messages 为 everyone/followers/friends，默认 everyone |
| GET `/account/qr/`、`/account/qr/image` | 实时从 uid 解析当前用户名，内容 `https://yitiao.org/@用户名/`，no-store |

互关为朋友。拉黑触发器原子清除双向关注；任一方向拉黑都阻止重新关注和私聊发信。解除不恢复关注。本人始终可看自己的四类名单；他人能看数量不代表能看名单。页面 k/m 仅格式化展示，接口为整数。`/account/` 显示朋友数；主页不显示自关注/自聊。关注按钮以服务端结果更新、失败显示原因；发消息入口在允许时显示，点击明确提示“聊天尚未开放”。

“粉丝”检查 **发送者关注收件人**，朋友再检查反向关注。组内共同发言不因私聊拉黑默认受限。创建私聊、原生邀请、后续私聊消息/编辑/撤回/反应、附件上传、YCard 赠送、转账创建/入金均重新检查。已入金资产收款/退款独立于消息撤回或隐私调整。

拒绝格式（HTTP 403）：

```json
{"errcode":"M_FORBIDDEN","code":"YICHAT_MESSAGE_FORBIDDEN","error":"由于对方隐私设置，你不能发送消息"}
```

未来客户端必须显示发送失败/红色感叹号；不得将拒绝后的本地气泡标记成功。修改隐私不改写历史。

## 会话与 Matrix

`POST /api/rooms`：`{kind:"direct"|"group",users:[当前用户名],name?:...}`。服务端建立短期一次性创建票据，模块在实际 create event 上注册 room/kind/owner/peer；不信任 m.direct、名称、人数。原生 createRoom 无票据被拒绝。私聊参与者固定为两位；群邀请本站账号且由 Synapse 校验邀请权限。

新群没有 encryption 状态，只收服务器可读消息；私聊强制 Megolm，不接受明文降级。旧加密群登记为 legacy_encrypted，继续接收密文并保留历史/备份。迁移步骤见 DEPLOYMENT；绝不通过删除一个状态假装关闭加密。

公开网关采用 Matrix 路由白名单：sync、versions/capabilities、whoami、keys upload/query/claim/changes/signatures/device_signing、room_keys、设备、加密 sendToDevice、自己的 account_data/filter、profile、已注册 rooms 的成员/历史/消息/状态，以及 join。Synapse 继续执行原生成员、历史可见性、power levels、设备和密钥规则。未认证管理/注册/登录/联邦路由不公开。明文 m.room_key、m.forwarded_room_key、m.secret.send 的 to-device 传输被拒绝，必须包在 Matrix 加密事件内。

群文字及编辑 body 最多 2000 个 Unicode 码点；发送正文与 m.new_content 均检查。私聊服务端只能限制密文 content 的规范 JSON UTF-8 大小 ≤65536 字节，不检查密文字数。未来客户端负责加密前 2000 码点限制。新群 supports `m.text/m.image/m.audio/m.video/m.file` 标准事件，附件使用下述接口。

## 共享请求与上传限制

同 uid 跨设备/token/连接/进程，共享 SQLite 原子 **滚动窗口 `(t-1秒,t]`**，最多 10 次 API 请求。可瞬时发 10 次，没有跨秒攒额度、后台排队或独立设备突发池。Matrix/业务 API、主站 social API 共享同一 rate DB；sync 仅 HTTP 建连时计数，最长 30 秒。静态文件不计数。匿名按 Caddy 提供的真实 IP 独立 10 次/秒。

HTTP 429 包含 `Retry-After`（向上取整秒），Matrix `M_LIMIT_EXCEEDED`、`retry_after_ms` 和 `YICHAT_RATE_LIMITED`。限流库不可用返回 503。

上传实际流量共享 500000 字节/秒，无初始突发；每次读取前用持久虚拟时钟预留额度，超大 ASGI 分块补计费，Uvicorn 提供有限传输缓冲。每用户最多 3 个上传，默认日接入额度 1073741824 字节（失败请求也计额度；chunked 先预留 100MB）。空闲 30 秒终止，租约 60 秒续期，崩溃遗留 .part 在确认无活动租约后清理。

`POST /api/files/{room}` 接受原始字节；长度可省略，始终按实际收到字节限制 **100000000**。私聊应上传客户端已加密字节，100MB 也按密文计算。返回 id、mxc URL、bytes、expires；服务端直接流式计算 SHA-256，建立 room/uploader/接收时间关联，不读取 E2EE 附件描述。

受控原生别名：POST `/_matrix/media/v3/upload` 或 `/_matrix/client/v1/media/upload` 必须附 `room_id` query 或 `X-YiChat-Room`，执行完全相同鉴权、隐私、并发/限速/实际大小管线并返回 content_uri。缺上下文拒绝。原生 download (`media/v3`、`client/v1/media`) 的本站 mediaId 映射到受鉴权文件下载，不访问 Synapse 原生媒体仓库。没有对象直传或公开对象端口。

发送附件消息时，content 外层设置 `org.yitiao.attachments: [id,...]`（最多100个），或网关 send 请求附 `X-YiChat-Attachments: ["id",...]` JSON 头，由网关写入该字段。私聊只关联不透明文件 id，不上传密钥或解密后的附件描述。模块核实实际上传者、会话、哈希 receipt 和实体；被拒绝事件不会形成已接受引用。

`GET /api/files/{id}/meta|content`：上传者可取自身未过期暂存对象；其他成员必须有覆盖至少一次实际接受引用时刻的 join 历史证据。后来加入不能补取旧引用，但能取加入后新分享的同一附件。未发送对象不向成员公开。各成员到期时间按其可访问的引用日结束+30天计算，不因其无权访问的后续引用延长。到期或已清理返回 410。

## 资产和预览

`POST /api/cards/gifts`：`{room,instance,recipient,key}`。真实 YCards instance 所有权、销毁/挂售/拍卖状态、当前成员、接收 uid 和幂等键在原账本事务中校验；更新 owner/acquired，复用原 YCards 清除公开展示的触发器，独立记录 gift。失败不转移；消息投递失败可单独重试，消息卡片不授权资产。

`POST /api/preview` 只提交 `{url,room?}`。白名单是核对源码后的真实路由：

- `https://yidt.yitiao.org/explorer/tx/{64小写hex}`
- `https://yitiao.org/ycards/card/{32小写hex}/{cid}/`
- `https://yitiao.org/games/parachute/share/{32位token}/`
- `https://chat.yitiao.org/orders/{32位token}`

严格 scheme/host/path、无端口/凭证/query/fragment/转义路径；只查权威数据库结构化字段，不取 DNS、HTTP、重定向或任意 HTML。订单要求原会话当前成员与 room 一致；卡片每次核实公开状态和当前持有人；游戏只读已结束分享。预览不触发资金或游戏动作。未来 E2EE 客户端在本地提取 URL，只提交必要 URL，不上传整段聊天或密钥。

归档路由、完整日期权限及本地校验见 ARCHIVE-V1；资金请求及幂等说明见 FUNDS。
