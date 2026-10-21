# YiChat 当前聊天内核接口文档

更新日期：2026-10-20。本文对应当前部署的网关、Synapse 模块、主站关系服务及 YIDT/YCards 适配器。示例中的用户、房间、token、金额和摘要仅说明格式，不代表真实交易或可用凭据。

## 1. 地址、范围与统一约定

| 系统 | 地址 | 职责 |
|---|---|---|
| 主站 | `https://yitiao.org` | OAuth、不可变用户 ID、资料、关注/拉黑/隐私 |
| YiChat | `https://chat.yitiao.org` | 认证网关、Matrix、上传、资金订单、赠卡、预览、归档 |
| YIDT | `https://yidt.yitiao.org` | 原钱包、绑定、付款确认、权威账本 |

现已提供 TypeScript Web 聊天客户端，入口为 `/`，使用原生 Matrix Rust Crypto。旧 `/orders/{id}` 地址直接返回聊天中的原会话，归档校验页继续可用；本次不提供 App 或归档导入/重发。服务不公开 Synapse admin、注册、联邦或原生媒体仓库端口。

- 下文除特别注明的主站路径，其余均以 YiChat 为 base URL。
- 业务请求通常使用 `Content-Type: application/json`；上传使用原始字节。URL 中 room/MXID/event_id 必须按单个路径段编码。
- 接口时间戳与归档日期以 **GMT+0（UTC）** 为准；Web 聊天、会话列表、订单和交易展示转换为客户端系统时区；归档日期选择及封存规则仍固定UTC。Unix 时间秒和毫秒均基于 UTC，日期为 `YYYY-MM-DD`。服务器本地时区、下载者时区和客户端 `origin_server_ts` 不决定归档日。
- **私聊和群聊统一30日过期**，按事件实际接受的 UTC 自然日计算：该日次日00:00 + 30×86400秒。例如2026-10-19日的内容，从2026-11-19 00:00:00 GMT+0起具备清理资格。不是私聊7日，也没有客户端自选保留期。
- 清理必须满足可靠存证及其他门控条件；到期不保证立刻物理删除。归档摘要、必要权限证据、当前状态/加密状态、金融和资产账本不按聊天正文删除。
- 转账收款和红包领取期限是独立业务时限，目前均为**入金确认后24小时**；不能把聊天30日保留期用于延长未收款资金。
- 金额对外使用十进制字符串，客户端不得转为浮点数运算。实际 YIDT 精度 `10^-16`，最低红包单份 `0.01`，gas `0.00390625 YIDT`。
- 业务接口成功默认 HTTP 200；失败检查 HTTP 状态和响应错误，不能以本地气泡或卡片认定成功。

## 2. 身份、登录和令牌

### 2.1 标识区别

| 标识 | 格式/含义 |
|---|---|
| uid | 主站不可变32位小写十六进制 ID；关系和业务参与者的长期主键 |
| username | 当前用户名，可用于搜索及显示，不应作长期身份主键 |
| MXID | Matrix `@...:chat.yitiao.org`；旧用户沿用原映射，新用户由 uid 确定性映射 |
| room_id | Synapse 生成的 `!...:chat.yitiao.org`；会话类型和参与者另由服务器登记 |
| event_id | 服务器实际接受的 Matrix 事件 ID |
| device_id | Matrix 设备；不等于主站登录 sid |

YiChat OIDC `sub=uid`；YIDT原有登录标识不变。不要根据用户名重建旧 MXID，也不要信任 `m.direct`、房名或人数来判断私聊。

### 2.2 OAuth 流程

1. 浏览器访问 `GET /auth/login`，307跳转主站 `/oidc/authorize`。
2. 使用主站已有登录/两步验证，完成 S256 PKCE、state、nonce 授权。
3. 主站回调 `GET /auth/callback?code=...&state=...`。网关验证签名、issuer、audience、有效期、一次性登录状态、uid/用户名和主站 sid。
4. 成功设置 Secure、HttpOnly、SameSite cookie `yc_session`，跳回 `/`。回调不能重复使用。
5. `GET /api/session` 取得当前设备的网关 token。未来 SDK 的 homeserver base URL 必须是聊天域名，access token 使用此 token，不能绕过网关连接8018。

示例响应（标识和时间为示意）：

```json
{
  "uid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "user": "alice",
  "mxid": "@alice:chat.yitiao.org",
  "device": "DEVICE_ID",
  "token": "OPAQUE_GATEWAY_TOKEN",
  "expires": 1795000000.0,
  "user_id": "@alice:chat.yitiao.org",
  "device_id": "DEVICE_ID",
  "home_server": "chat.yitiao.org",
  "funds_enabled": true,
  "upload_rate": 500000,
  "max_file": 100000000,
  "packet_ttl": 86400,
  "retention": {
    "days": 30,
    "timezone": "GMT+0",
    "basis": "utc_day_end",
    "cleanup_requires_sealed_archive": true
  }
}
```

这里 `user_id` 是 Matrix兼容字段，值为MXID，不是32位主站uid；主站uid从关系接口、签名身份及业务参与者字段获得。实际过期还受主站 sid 有效性约束，不能仅依据 expires 离线推断可用。

### 2.3 鉴权与退出

业务及 Matrix 请求使用同源 cookie，或 `Authorization: Bearer <网关token>`。不要把 token 放进 URL；代理会移除 query 中 access_token。同源浏览器写请求必须有正确 Origin；原生 App 用 Bearer 且不带 Origin。未开放跨域 CORS。

- `POST /api/logout`：撤销当前主站sid关联的全部 YiChat会话并清除聊天cookie；响应 `{"ok":true}`。它不等同于全站退出。
- 主站 `POST https://yitiao.org/logout/`：退出主站并撤销该sid关联OAuth凭证；所有关联 YiChat设备每次请求被拒绝，长轮询返回前再次检查。
- 模块持续重试撤销对应原生Matrix token，保留设备、验证和密钥备份；不借用会删除设备密钥的原生 `/logout`。
- 依赖/权限库异常不得放行。新登录或重新授权不能替代未来客户端自行保存加密密钥。

## 3. 主站关系、名单隐私和二维码

本节 base URL 是 `https://yitiao.org`，使用主站会话。写请求另需 `X-CSRF-Token` 或表单 `csrf`，值来自页面 meta/隐藏字段；不是 YiChat token。

| 方法、路径 | 请求 | 响应/行为 |
|---|---|---|
| GET `/api/social/{uid}` | 可匿名读公开状态 | 精确数量、当前访问者关系、名单权限和发信许可 |
| POST `/api/social/{uid}/follow` | `{}` | 幂等关注 |
| POST 同路径 `/unfollow` | `{}` | 幂等取消关注 |
| POST 同路径 `/block` | `{}` | 清除双向关注并拉黑 |
| POST 同路径 `/unblock` | `{}` | 解除本人发起的拉黑，不恢复关注 |
| GET `/api/social/{uid}/lists/{kind}?cursor={uid}` | kind=`following/followers/friends/blocked` | 每页30，`items`、`next_cursor`，无下一页为null |
| POST `/api/social/block-username` | `{"username":"bob"}` | 先解析uid，再执行拉黑 |
| GET/POST `/account/privacy/` | POST表单：三个名单字段勾选值`on`，`messages` | 保存后302到同页 `?saved=1` |
| GET `/account/qr/` | 本人认证 | 居中二维码页面 |
| GET `/account/qr/image` | 本人认证 | 透明底白色码点PNG，no-store |

社会关系响应形状：

```json
{
  "uid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "username": "bob", "nickname": "Bob", "biography": "",
  "counts": {"following": 12, "followers": 34, "friends": 5},
  "lists_visible": {"following": false, "followers": false, "friends": false},
  "is_self": false, "following": true, "mutual": false,
  "blocked": false, "can_message": true, "chat_available": true
}
```

`blocked` 表示访问者是否主动拉黑对方；`can_message` 已综合双向拉黑和收件人隐私，不应仅依据 blocked 字段推断。`chat_available=true` 表示 Web 聊天入口已开放，与 YIDT 资金开关独立。

名单页结果：`{"items":[{"uid":"...","username":"bob","nickname":"Bob"}],"next_cursor":null}`。cursor按uid排序，每页重新鉴权；本人始终可看，黑名单始终只对本人开放。三个名单默认均不公开，公开数量不等于公开名单。显示k/m不改变接口整数。

`messages`：`everyone`默认所有人；`followers`要求发送者关注收件人；`friends`还要求反向关注。禁止自关注/自拉黑，禁止重复关系；任一方向拉黑优先禁止重新关注和私聊发信。群共同发言不因此默认被禁。隐私变化不追删历史。

二维码编码固定当前 `https://yitiao.org/@用户名/`，随用户名改变。二级账户页面仅渲染本页选项，主页摘要/签到/钱包和其他菜单不重复进入二级页。

## 4. 用户查询与资料

| 方法、路径 | 参数 | 成功结果 |
|---|---|---|
| GET `/api/users?q=ali` | 前缀长度1～50；其余返回空数组 | 最多20项，`username/nickname/mxid` |
| POST `/api/profiles` | `{"mxids":["@alice:chat.yitiao.org"],"usernames":["bob"]}`，两类输入合计最多100个字符串 | 去重、忽略未知或已清除用户，返回资料数组 |
| GET `/api/avatars/{username}` | 认证 | image/jpeg；未设置头像404 |

资料项：`username`、`mxid`、`nickname`（无昵称时回退用户名）、`biography`、`profile_url`、`avatar_url`。头像URL可能带缓存版本参数，无头像为null。以上聊天接口均需要认证；公开主页仍由主站 `/@用户名/` 提供。

## 5. 会话创建、邀请与消息

### 5.1 业务会话 API

`POST /api/rooms`：

```json
{"kind":"direct","users":["bob"],"name":"可选会话名"}
```

kind为`direct`或`group`；users输入1～50个当前用户名，服务器去重并排除本人，私聊最终必须恰好一位对方。群可提供多个用户。返回原生 `{"room_id":"!...:chat.yitiao.org"}`。name截到100码点，不决定权限。

服务端分配短期创建票据，Synapse模块在实际create事件上登记kind、owner、peer及加密模式；直接原生createRoom没有有效票据被拒绝。未传 key 的旧请求仍没有幂等保证。Web 新增可选 `key`（16～100位 A-Za-z0-9_-），按 uid+key 和固定 payload 绑定原创建票据；相同请求返回原 room_id，不同 payload 409，原请求处理中返回 409/YICHAT_ROOM_CREATE_PENDING。不能换新键盲目重建。`GET /api/room-creations/{key}` 仅本人可查，返回 state=`created/pending/not_created`、room_id、retry_after_ms；not_created 只在票据过期且没有实际登记房间时返回。

- `GET /api/rooms`：返回本人的当前已加入、由服务器登记的会话元数据数组；由原生 joined_rooms 核实当前成员，邀请仍来自 sync。
- `GET /api/rooms/{room}`：当前成员认证，返回`room/kind/owner/peer/days/encryption`；owner/peer为MXID，群peer为null，days固定30，encryption为`direct/plain/legacy_encrypted`。
- `POST /api/rooms/{room}/invite`：`{"username":"carol"}`；仅群聊，实际邀请权由Synapse power levels检查，成功通常`{}`。不能邀请第三方标识或外部用户。
- 接受邀请/加入使用原生Matrix join，最终状态以服务器sync/member事件为准。
- 私聊参与者固定，不允许第三人、代踢另一方或改变加密策略；允许本人离开。

### 5.2 消息和加密

标准发送路径：`PUT /_matrix/client/v3/rooms/{room}/send/{eventType}/{txnId}`。成功为`{"event_id":"$..."}`。同一逻辑消息在同一设备重试使用原txnId；发送成功后再建立业务关联，不能把消息事务ID代替资金幂等键。

群文字示例：

```json
{"msgtype":"m.text","body":"你好"}
```

群编辑示例（`m.room.message`）：

```json
{
  "msgtype":"m.text", "body":"* 修改后的文字",
  "m.new_content":{"msgtype":"m.text","body":"修改后的文字"},
  "m.relates_to":{"rel_type":"m.replace","event_id":"$original"}
}
```

群body及编辑m.new_content.body各最多2000个Unicode码点。图片/音频/视频/文件沿用Matrix `m.image/m.audio/m.video/m.file`格式和受控上传。撤回使用 `PUT .../redact/{eventId}/{txnId}`；使用与原发送不同的事务ID（每个撤回操作内部重试保持稳定）。服务端以实际接受时间限制5分钟，超过返回403/YICHAT_REDACTION_WINDOW；转账、红包、赠卡业务消息返回403/YICHAT_BUSINESS_IMMUTABLE。规则在Synapse模块执行，不能通过原生token绕过。撤回是独立事件，不修改旧封存文件或资产记录。

私聊必须发送`m.room.encrypted`，使用原生Megolm及设备密钥/验证/加密备份。服务器不接受私聊明文降级，只能限制密文content序列化UTF-8体积≤65536字节，不能检查密文字数。未来客户端加密前自行限制2000码点。

新群无`m.room.encryption`，服务器可读，经HTTPS传输；不允许后加E2EE来绕过策略。旧加密群维持`legacy_encrypted`，不能宣称删除状态即解密。迁移需要另建明文群、重新邀请并明确切换，不自动重发或解密旧消息。

### 5.3 Matrix 路由兼容范围

| 路径族（相对 `/_matrix`） | 说明 |
|---|---|
| GET `client/versions` | 匿名发现可用；有有效令牌时仍计入uid限流 |
| `client/v3/sync` | 原生sync，timeout限制0～30000ms，返回前复核主站会话 |
| `client/v3/capabilities`、`account/whoami` | 原生能力、MXID和设备 |
| `keys/upload/query/claim/changes/signatures/upload/device_signing/upload` | 原生设备公钥、交叉签名等；仍由Synapse授权 |
| `room_keys/...` | 原生加密密钥备份版本及加密备份内容 |
| `sendToDevice/{type}/{txn}` | 仅加密Olm、验证及协议控制信令；不是聊天通道 |
| `devices[/device]` | 原生设备管理，敏感操作可能要求原生验证 |
| `user/{id}/account_data/...`、`filter[...]` | 由Synapse核实本人权限 |
| `profile/{id}[/displayname]` | 原生资料路径；主站完整资料推荐/api/profiles |
| `rooms/{room}/...`、`join/{room}`、`pushrules/...` | 注册会话、原生成员/历史/power规则及模块限制 |

路由白名单还兼容代码声明的r0/unstable路径族；新客户端优先用v3。开放某一路径族不表示每个方法或子接口一定成功，底层版本和认证规则仍适用。`m.direct`仅是客户端元数据，不能改变服务器会话类型。

禁止直接明文to-device发送`m.room_key/m.forwarded_room_key/m.secret.send`，密钥材料需在Matrix加密传输内；既有E2EE房间允许必要的密钥恢复信令。Web 已使用 matrix-js-sdk 的 `initRustCrypto()`；未来 App 使用 matrix-rust-sdk。浏览器设备验证、Secret Storage 和加密密钥备份均使用原生协议。

## 6. 请求和上传限制

同一不可变uid的全部设备、token、连接、API实例共用**10请求/滚动秒**，窗口`(t-1秒,t]`。最多瞬时10个，没有积攒额度。业务/Matrix/主站social API共享；sync仅建立请求时计一次，连接存活期间不重复计数。静态资源不计，匿名按真实IP独立计数。

429示例：

```http
Retry-After: 1
Content-Type: application/json
```

```json
{"errcode":"M_LIMIT_EXCEEDED","code":"YICHAT_RATE_LIMITED","error":"请求过于频繁，请稍后再试","retry_after_ms":450}
```

上传每uid共享500000字节/秒、最多3个并发；单文件100000000字节，按实际收到的字节校验，私聊按上传密文大小计算。默认每日接入额度1073741824字节，失败也占额度，未知长度先预留100MB；日界GMT+0。空闲30秒中止，有租约保护及中断临时对象回收。客户端遵守Retry-After并加抖动，不能换设备绕过。

## 7. 附件上传、关联和下载

`POST /api/files/{room}`，body为原始字节，可带Content-Length。需当前成员和实时私聊发信许可，完成前再次复核。成功：

```json
{"id":"48位小写十六进制ID","url":"mxc://chat.yitiao.org/文件ID","bytes":12345,"expires":1795046400}
```

原生别名 `POST /_matrix/media/v3/upload` 或 `/_matrix/client/v1/media/upload` 必须传query `room_id`或头`X-YiChat-Room`，使用相同管线，额外返回`content_uri`。原生直传、远程媒体抓取、公开对象端口均未开放。

上传后发送消息，在content外层添加：

```json
{"org.yitiao.attachments":["48位文件ID"]}
```

或者发送请求附头 `X-YiChat-Attachments: ["48位文件ID"]`，网关注入该外层字段。最多100项，只能关联本人在同一会话上传、尚未到期且有实际哈希/实体的文件。私聊先在客户端加密文件，密钥和完整附件描述保留在加密正文内；仅必要文件ID在外层关联，不上传整段聊天或解密密钥。

上传成功不代表消息已发送；只有服务器实际接受的消息才形成引用。跨日上传/发送按**引用事件接收日**进入归档，不把未发送草稿发布到群归档。

| 方法、路径 | 结果与权限 |
|---|---|
| GET `/api/files/{id}/meta` | 原file记录字段及sha256、实际访问者expires、expired；只读元数据不等于可取过期实体 |
| GET `/api/files/{id}/content` | 原始上传字节、octet-stream、Content-Length；需有权的接受引用历史 |
| GET `/_matrix/media/v3/download/chat.yitiao.org/{id}` | 同一鉴权下载管线 |
| GET `/_matrix/client/v1/media/download/chat.yitiao.org/{id}` | 同上 |
| GET `/_matrix/media/v3/config` 或`/_matrix/client/v1/media/config` | `{"m.upload.size":100000000}` |

本人可取自己的未过期草稿；他人需在至少一次实际接受引用时已join。上传后加入但分享前加入者可以取新分享，不能补取无权旧引用。每人expires按其可访问的引用日结束+30天计算；共享实体至少保留到最后接受引用日结束+30天。未到期但实体异常缺失503，已到期/清理410，无访问权限403。

## 8. YIDT 钱包、私聊转账与群红包

### 8.1 当前启用状态与真实账本

本轮已获授权启用生产YiChat资金功能，配置使用原账本已存在的官方托管钱包 `yidt-000000000000`，没有另建余额或新托管地址。运行时以 `/health`、`/api/session`、`/api/wallet` 的enabled字段为准；缺配置、对账异常或依赖失败仍拒绝资金操作。

YIDT实际实现是中心化哈希链账本，沿用userdata.db、钱包控制验证、绑定和escrow。账本事务提交是当前确认点；没有外部RPC、块高确认深度或外部链重组协议。不要把当前数据库事务能力类推为外部链原子性。

`GET /api/wallet`：

```json
{"enabled":true,"binding":null,"bind_url":"https://yidt.yitiao.org/services/yichat/"}
```

响应另含 `fees.incoming`、`fees.outgoing_per_share`，均由当前权威账本 GAS 常量格式化为十进制字符串，供创建前确认；创建结果和实际授权金额仍是最终依据。无绑定不代表全局资金关闭。用户应到bind_url，按原钱包控制/归属验证和支付密码机制绑定；不能仅填写任意接收地址。已绑定时binding是钱包地址字符串。

### 8.2 创建与明确授权

`POST /api/orders`：

```json
{"room":"!room:chat.yitiao.org","key":"a-unique-request-key-0001","amount":"1.23","title":"祝你好运","count":3}
```

room必填，类型从服务器获取。私聊生成转账，接收者自动取对方，count固定1；群生成红包，count为1～1000整数。amount为最多16位小数的非负十进制字符串，本金必须≥0.01×count。title最多100码点。key为16～100位`A-Za-z0-9_-`，同发送uid/key与相同payload重试返回原订单，不同payload409。

发送者必须当前成员、主站sid有效、钱包已验证绑定且余额足够。私聊收件人也须绑定，检查实时发信隐私。服务器冻结双方uid、room、源/目标钱包、本金、费用及授权intent；更换绑定不能静默改变资金去向。

流程：

1. 创建返回`awaiting_deposit`及`authorize_url`，此时没有扣款。
2. 打开实际YIDT `https://yidt.yitiao.org/authorize/{intent}`，明确查看金额、钱包和费用并批准；按原绑定锁定规则要求支付密码。此页面的GET不付款。
3. 批准后回订单页，发送者 `POST /api/orders/{id}/fund`，body `{}`。核实原sid、intent、固定绑定stamp、精确金额和余额，真实入金到官方托管钱包。
4. 当前账本实际入金提交后，私聊为`awaiting_receipt`、群为`active`，才开始相应24小时时限。

本金与费用独立：入金gross=本金+count笔出金gas预留，发送者另外支付入金gas。领取净到账等于分配本金，不从本金里扣手续费。不要重复批准/重建订单来处理未知响应，先查原id。

### 8.3 查询与操作

| 方法、路径 | 请求 | 行为 |
|---|---|---|
| GET `/api/orders/{id}` | 无 | 需原会话当前成员；真实详情和actions |
| POST `/api/orders/{id}/fund` | `{}` | 发送者原付款sid、已批准intent，执行入金 |
| POST `/api/orders/{id}/receive` | `{"room":"原room"}` | 仅指定私聊接收uid、当前成员、原目标绑定钱包，确认才预留出金 |
| POST `/api/orders/{id}/claim` | `{"room":"原room"}` | 仅当前所属群成员，点击即确认领取 |
| GET `/orders/{id}` | 浏览器 | 303 跳转 `/?order={id}`，认证后读取订单并打开原会话；授权返回不入金，需用户明确确认 |

详情字段：

| 字段 | 含义 |
|---|---|
| id、url、room、kind、title | 固定订单标识及 `chat.yitiao.org/orders/{id}` 地址 |
| sender_uid、recipient_uid | 长期参与者；红包recipient_uid为null |
| sender、recipient | 创建时用户记录；群recipient为null |
| source、destination | 固定付款/私聊收款钱包；群destination为null |
| amount、count、state、expires | 本金字符串、份数、状态、入金后截止秒时间；入金前expires为null |
| deposit | 真实入金交易hash，未入金为null |
| incoming_fee、outgoing_fee_reserve | 入金gas和预留出金费，十进制字符串 |
| claims | 已分配列表：user、user_id（主站uid）、principal（最小单位字符串）、amount、state、tx、kind |
| best、my_amount | 已分配结果中的手气最佳用户名、本人已分配金额；无结果null |
| refund | 无退款时null，否则principal最小单位字符串、amount十进制字符串、state、tx |
| actions.fund/receive/claim | 当前观察到的可用动作；真正操作仍重新鉴权，可能因并发或绑定变化失败 |
| authorize_url | 发送者待付款时的YIDT授权页面，否则null |
| receipt_ttl_seconds | 私聊收款期限秒，群为null；默认86400 |

群领取：同uid、同钱包各只能一次，发送者账号及源钱包不能自领；URL不是领取凭证。最低额m、剩余R、份数k：k=1取R，否则安全随机均匀取`[m,min(floor(2R/k),R-(k-1)m)]`。资格、随机结果、记录和份数/余额在同一事务提交；不提前公开未领取结果。

### 8.4 状态机、失败重试和退款

- 私聊：`awaiting_deposit → awaiting_receipt → paying → paid`。
- 群红包：`awaiting_deposit → active → paying → paid`。
- 超时未收款/未分配余额：`refunding → refunded`；详情可能先显示推导状态`expired`，后台随后持久执行退款。
- 领取预留完成后即固定金额，出金失败不重新抽奖。outbox有持久重试时间和唯一reference，未知结果先查权威账本，偏离即停止并对账，不盲目重付。
- 退款只包括未分配本金；已分配待出金金额保留。未用预留费用单独计算并扣退款gas。最终领取本金+退款本金=入金本金。
- 后续隐私变化不追取消已入金承诺；receive/claim仍核实指定参与者/群成员及钱包。聊天撤回、过期或归档清理不改资金账本。

## 9. YCard 赠送与结构化预览

`POST /api/cards/gifts`：

```json
{"room":"!room:chat.yitiao.org","instance":"32位小写hex实例ID","recipient":"bob","key":"unique-card-gift-key-0001"}
```

`GET /api/cards/gifts/by-key/{key}` 可由原发送者 uid 查询已完成的赠送，供响应丢失后的只读核实；他人404，不重新转移资产。返回原记录及 room/recipient。

成功 `{"id":"赠送记录ID","instance":"实例ID","recipient_uid":"接收uid","state":"transferred"}`。检查发送者所有权、未销毁/挂售/拍卖、接收者、当前实际成员及私聊权限，原账本事务原子转移所有权并记录。key规则同资金key，重复相同请求返回原结果；相同key不同payload409。消息或卡片渲染失败可独立重试，不再次转移资产。

`POST /api/preview`：`{"url":"完整URL","room":"可选会话ID"}`。room存在则先查成员和发信权；订单预览必须提供匹配room。最多2048字符，仅解析以下严格路由：

| 类型 | URL |
|---|---|
| transaction | `https://yidt.yitiao.org/explorer/tx/{64位小写hex}` |
| card | `https://yitiao.org/ycards/card/{32位hex实例}/{cid}/` |
| game | `https://yitiao.org/games/parachute/share/{32位token}/` |
| order | `https://chat.yitiao.org/orders/{32位token}` |

仅查权威数据库，返回type/url/data，非订单可能带title。未知、不公开或不匹配返回JSON null，越权订单403。transaction data是hash/source/destination/credit/gas/created（credit/gas为账本最小单位字符串）；card 现返回真实实例的 id/cid/tid/name/geometry/defects/design，design 排除 cost，权限与主站公开卡片完全一致；game 为 status/exit_height/crash_height/started_at，另含 snapshot=true 和 render_version=parachute-scene-v1，活动局不返回；order data为上述订单详情。

禁止额外端口、用户名密码、query、fragment、转义路径或任意host。不做外部HTTP/DNS/重定向抓取，不嵌入任意HTML，不触发资金或游戏操作。未来E2EE客户端本地提取链接，仅提交必要URL，不上传完整私聊正文或密钥。

## 10. 归档、摘要、附件与本地验证

### 10.1 路径和权限

令 `{prefix}=/api/archives/{room}/{YYYY-MM-DD}`：

| 方法、路径 | 参数/结果 |
|---|---|
| GET `{prefix}/summary` | query `spec=events-manifest`（默认）或`with-attachments`，认证可信摘要 |
| GET `{prefix}/download` | 相同spec，完整ZIP，Content-Length及X-Archive-SHA256 |
| GET `{prefix}/snapshot` | 仅GMT+0当天，provisional事件快照，不等于整日完整文件 |
| GET `/archives/` | 最小下载/本地哈希校验网页 |
| GET `/tools/verify_archive.py` | 下载本地Python校验工具，无密钥 |

下载和摘要要求覆盖**完整UTC日**的join历史，不能只看当前是否成员。中途加入、退出再加入或建房当天不满完整日会被保守拒绝。当前不提供部分导出接口；不能通过拼补无权部分冒充完整日。快照和附件等待实时源fence被持久游标追平，未追平返回503。

summary字段：room、day、version=1、spec、sha256、bytes、event_count、first_sequence、last_sequence、attachment_sha256、sealed_at、state=sealed、permission_spec、fence_sequence；另外返回cleaned_at、error、has_gaps。日期及时间按JSON ISO格式，sealed_at不写进ZIP。已清理实体后仍能查原摘要。

snapshot字段：state=provisional、spec=`snapshot-events-only-v1`、room_id、day、first_sequence、last_sequence、range_start_ms、range_end_ms、sha256、events_ndjson、complete_day=false。当天快照没有封存保证，不能与整日ZIP摘要混用。

### 10.2 确定性格式与持久化

归档只来自Synapse实际接受的事件，日期依据`received_ts`，稳定顺序依据持久分配的stream_ordering；重试去重，不导出动态sync。编辑和撤回是独立引用事件，归入其接收日，不重写旧封存内容。

v1 ZIP固定路径：archive.json、events.ndjson、attachments.json；with-attachments再含attachments/{id}。事件、文件和字段排序、UTF-8无BOM、紧凑JSON、单LF、ZIP_STORED/固定时间/固定ZIP64元数据都已固定，不包含下载者/设备/随机包装/动态导出时间。两个规格必须使用各自摘要。

私聊保存原始密文事件及必要加密字段，不保存解密密钥；群保存服务器接受的规范化事件。清单列id/bytes/sha256/received_ms/uploader/event_ids，只列本日已接受引用，白天上传次日发送进入次日归档；服务器从实际上传字节算哈希，私聊哈希密文。缺失或哈希不符不得生成完整含实体规格。

GMT+0日结束、可靠源fence追平和上传receipt完整后封存。ZIP先fsync/rename再提交PG不可变摘要；多实例去重，崩溃可确定性补完。采集失败、时钟回退、晚到已封存日、缺口和摘要不符会阻止相关清理并告警。

达到日结束+30天后，只有两个完整规格摘要可靠、实体复核一致、源健康、原生purge任务完成才清理。保留摘要/权限/审计，保留必要房间状态/加密状态和金融资产账本。备份/缓存/对象版本和用户已下载副本有明确例外，不宣称清除了全部用户副本。

### 10.3 本地工具

```bash
python3 verify_archive.py ./yichat-2026-10-19-v1-with-attachments.zip \
  --room '!room:chat.yitiao.org' --day 2026-10-19 --spec with-attachments
```

工具交互读取token（也支持YICHAT_ACCESS_TOKEN环境变量），仅向固定认证服务器GET摘要，拒绝重定向，本地流式计算文件SHA-256和字节数。输出VERIFIED/MISMATCH，退出码0/1。文件、私聊正文和密钥不上传。清理后摘要仍可验证已下载文件，下载本身返回410。

SHA-256仅证明与服务器存证一致，不证明服务器不可作恶或第三方不可抵赖。未来私聊恢复还需要对应独立加密密钥备份，只能展示校验过的密文实际解密所得内容。当前没有网页恢复、私聊解密或旧消息重发。

## 11. 错误和客户端处理

| HTTP/稳定码 | 含义与处理 |
|---|---|
| 401，通常M_UNKNOWN_TOKEN | 主站/聊天凭据失效，重新OAuth；不得继续资金写入 |
| 403 / YICHAT_MESSAGE_FORBIDDEN | 由于对方隐私设置，你不能发送消息；未来客户端显示发送失败红色感叹号 |
| 403 / YICHAT_LIST_PRIVATE | 对方未公开名单，不能用公开数量推断可读名单 |
| 403 / YICHAT_PARTIAL_HISTORY | 没有完整日权限，不得补齐导出 |
| 400 / YICHAT_SELF_RELATION、YICHAT_INVALID_CURSOR等 | 修正业务参数，不盲重试 |
| 409 | 幂等payload冲突、绑定变化、未授权、余额不足、订单不可操作或未封存；读取error判断 |
| 410 | 正常到期/清理，不能伪造下载成功 |
| 413 | 实际上传大小/请求体/密文事件体积超限 |
| 429 / M_LIMIT_EXCEEDED | 遵守Retry-After/retry_after_ms，业务写入保留原幂等键 |
| 503 / YICHAT_DEPENDENCY_UNAVAILABLE | 依赖暂不可用，拒绝而非默认放行 |
| 503 / YICHAT_ARCHIVE_UNAVAILABLE | 模块存证不可用，发送暂停 |
| 503 / YICHAT_HISTORY_PENDING | 历史权限尚未同步，稍后重试 |
| 503 / YICHAT_ARCHIVE_QUARANTINED | 已封存日期发现缺口，完整下载和清理暂停 |

普通业务拒绝可能使用`YICHAT_REQUEST_REJECTED`，模块策略拒绝可能为`YICHAT_POLICY_REJECTED`；底层Synapse错误保持Matrix格式。客户端不要假设所有409都有一个相同业务码，也不要把HTTP200的JSON null预览当成资产操作成功。

隐私拒绝完整示例：

```json
{"errcode":"M_FORBIDDEN","code":"YICHAT_MESSAGE_FORBIDDEN","error":"由于对方隐私设置，你不能发送消息"}
```

## 12. 运维与实现位置

| 文件/命令 | 用途 |
|---|---|
| backend/app.py、security.py、policy.py | 网关、鉴权、共享限流、房间及发信策略 |
| account_social.py | 主站稳定uid、关系、隐私和页面 |
| backend/synapse_module.py | 原生准入、成员屏障、token撤销、可靠事件采集 |
| backend/funds.py、assets.py、previews.py | 真实账本、订单/outbox、赠卡、白名单预览 |
| backend/archive*.py、deploy/archive.sql | 固定规格、PG存证/角色、封存及门控清理 |
| `python -m backend.manage status` | schema、功能开关、限额、采集水位、存证及缺口 |
| `python -m backend.manage reconcile` | 只读核查权威账本、订单及escrow，不修改余额 |
| `python -m backend.manage archive-once` | 使用worker角色执行一次封存/清理门控 |
| deploy/backup.py | 私密配置/原密钥、各数据库在线备份与PG dump |

生产配置只由root读取，原密钥不轮换、不进前端、日志或Git。API/采集/封存/清理分用PG角色；普通API无封存摘要修改权限。内部`/_synapse/client/yichat/fence`需要管理凭据且不对公网开放，不是客户端接口。

补充规格：[ARCHIVE-V1.md](ARCHIVE-V1.md)、[FUNDS.md](FUNDS.md)、[DEPLOYMENT.md](DEPLOYMENT.md)、[SECURITY-AND-OPERATIONS.md](SECURITY-AND-OPERATIONS.md)。历史测试记录见TEST-RESULTS.md，本轮实际结果另见ACCOUNT-UPDATE-20261019.md。

## 13. Web 只读补充与运行方式

- `GET /api/cards/mine?page=1`：认证本人卡片分页，沿用原 YCards 查询，每页24项；标注 giftable（当前未挂售/拍卖，最终赠送仍重新核实）。不自动公开本人卡片。
- `GET /api/cards/{instance}/render`：复用原 Store.preview 的权限和真实设计/实例/损耗数据。无权404，不修改资产。
- `/api/preview` 的 card 路径在一个读取快照内检查公开权限、所有权及损毁状态，再使用已有渲染数据；转手撤销公开预览。
- HTML/API no-store；内容哈希 assets 长缓存，WASM 仅允许同源编译。页面使用同源图片和 Blob 媒体，不嵌入整页 iframe 或第三方 HTML。
- 归档网页不导入、不恢复、不重发历史。独立的加密密钥恢复只调用 Matrix 加密备份；实时气泡不标作每日归档验证通过。

部署和回滚见 [WEB-DEPLOYMENT.md](WEB-DEPLOYMENT.md)，实现和边界见 [WEB-IMPLEMENTATION.md](WEB-IMPLEMENTATION.md)，本轮实测见 [WEB-TEST-RESULTS.md](WEB-TEST-RESULTS.md)。


## 14. 数据包增量更新与在线成员补齐（供后续原生 App 使用）

Web 仅调用 `/api/history/updates` 核对服务器撤回元数据；不调用 packets/exchange，不缓存、请求或提供成员历史包。原生 App 尚未开发，以下内核管线保留供 App 后续集成。Web 所需的本地设备密钥、待发消息和业务幂等记录仍保留；可选 Matrix 密钥备份只恢复解密能力，独立于消息自动备份。

这是一条认证的数据包补齐管线，不是归档文件导入、消息重发或自定义加密协议。原生Matrix同步继续负责实时聊天。接口均受同一uid的10次/秒限制、主站sid和房间/历史权限约束。

| 接口 | 输入/输出 |
| --- | --- |
| GET `/api/history/updates?since={ms}&after={sequence}&until={ms}` | 每页扫描100条可信摘要，返回有权事件的records、until和next_after。since=0为首次；until=0由服务器固定一致的窗口。摘要回填或源存证未完成503，不推进客户端完成时间。 |
| POST `/api/history/packets` | `{ids:[event_id]}`，每批1～20。服务器仍有原事件时直接返回Base64的原canonical事件字节（含末尾LF）、SHA-256和source=server。已撤回正文不再分发，返回认证tombstone。 |
| POST `/api/history/exchange` | `{requests:[request_id],offers:[{request,raw}]}`，最多64个等待票据、每次最多提交1个包；返回asked、fulfilled、pending及20秒等待建议。只接受明确清理后的缺失事件。 |

Web 仅保存最后完成的服务器时间及房间游标；正常撤回检查重叠5分钟，重新进入/登录重叠10分钟，首次及长时间离线最多检查最近31天的元数据。原生同步/历史分页读取服务器保留消息。旧版 Web 已保存的数据包不读取、不继续提供给成员，也不擅自删除。App 的自动备份、待补齐列表持久化、存储容量和恢复界面将在 App 实现阶段独立验收。

可信摘要来自原接受事件和既有统一存证 `archive.canonical(event)`，不是其他用户声称的哈希。Synapse轮询及清理前均保留每包哈希/必要元数据，不新增永久正文仓库。现存30日内数据直接由服务器提供；正文暂缺但没有完整清理证据返回503，不请求其他用户。确已到期清理、且拥有可信摘要的包才生成短期请求，在线且有相同历史权限的其他用户提供本地保存的原包，经服务器校验哈希、事件ID、房间后临时转交；接收客户端再次校验，再交Matrix SDK解密。私聊/旧加密群只接受 `m.room.encrypted` 原密文包，明文正文和解密密钥不上传。撤回控制信息由服务器保留的认证元数据提供，不向成员索取私聊明文控制包。

服务端转交缓冲仅在内存，最多256项、每位请求者64项、总数据8MiB、TTL120秒；未来 App 客户端应合并检查；无人在线或本地无副本时应明确显示待补齐，不能承诺恢复不存在的副本。新机制部署前就已彻底清理、且没有单包可信摘要的历史不接纳自报哈希，不能伪造可恢复性。

新业务关联使用消息外层 `org.yitiao.business={kind:"order"|"gift",id:"权威业务ID"}`。SDK加密适配仅把最小业务引用置于外层，完整正文/赠卡信息仍在私聊密文内；服务器核验真实记录及同一room，不执行资产操作。群内可读的真实订单链接也受撤回保护。旧私聊中的任意URL不能由服务器解密分类；旧消息早已超过5分钟，同样不能撤回。

`GET /api/cards/gifts/{id}?room={room}`：真实赠卡收据及卡面快照，复用原YCard渲染输入；需要当前房间权限及赠卡当时历史权限（双方可查本人收据）。新赠卡在同一资产事务中保存去成本信息的卡面快照，反复渲染不再次转移资产。旧赠卡无快照时只读取原权限仍允许的卡面，缺失明确降级。


## 15. 会话中的转账、红包及收款回执

- 转账/红包原始消息保留订单 URL，并含 `org.yitiao.share={type:"order",url}` 和最小外层业务引用。历史纯 URL 也可渲染原生资金气泡；点气泡打开真实订单和服务端 actions。
- YIDT 授权使用当前标签页，旧返回地址 `/orders/{id}` 进入 `/?order={id}`。客户端认证后 GET 原订单，依据返回的 room 打开会话。明确确认 fund 成功后发送气泡；刷新、创建、预览和授权返回均不自动 fund。
- 接收者明确 receive 后，以独立且本地持久的消息 txn 发送 `{type:"receipt",id,url}` 分享及原订单 business 引用。私聊按原生 E2EE 加密。回执渲染重新读权威订单，必须匹配 `recipient_mxid`、recipient_uid 和实际 claims；付款未完成时显示“到账处理中”，不会显示“已到账”。响应丢失先查询原订单；回执重试不调用 receive/fund。
- `GET /api/orders/{id}` / preview order data 新增 `recipient_mxid`（群红包为 null），用于核验回执消息发送者，不替代主站 UID 或其他资金权限。
- Web 业务操作记录和待发箱沿用原稳定 txn；发送失败在待发箱重试，后续新设备/App 的全局回执去重不以 Web 本地记录作保障。资金幂等与到账状态始终由原账本保证。

历史 packets/exchange 的原生传输约束：必须携带 Bearer 网关 token，且不能包含浏览器 Origin 或 Sec-Fetch-Mode；旧 Web 标签页亦返回 403/YICHAT_APP_ONLY。updates 仍允许 Web 核对撤回元数据。这是客户端范围约束，不是 App 身份证明，所有原权限、可信摘要及服务器优先规则继续执行。


## 16. 未入金订单取消（2026-10-20）

- `POST /api/orders/{id}/cancel`，请求 `{}`，返回完整权威订单。仅付款人（冻结的主站 UID）可操作，必须是有效主站会话和当前房间成员；不要求沿用创建时 SID，取消不涉及资金转移。
- `GET /api/orders/{id}` 的 `actions.cancel=true` 表示可以取消：付款人且 `state=awaiting_deposit`、没有 deposit。资金功能关停时仍允许放弃未入金订单；其他 actions 仍按原开关执行。
- 取消与 fund 使用同一权威账本 `BEGIN IMMEDIATE` 事务串行化。成功后 `state=cancelled`、无 deposit，未分配金额/份数归零；本金、参与者、幂等键等不可变订单字段保留。对应 pending/approved YIDT intent 同事务置 cancelled，旧授权页不能再次批准。没有任何入金、退款、出金或发送消息操作。
- 重复取消同一订单返回同一 cancelled 结果；重新使用原创建幂等键仍返回原已取消订单，不能借重试创建新订单。用户明确开始新操作时使用新的独立业务幂等键。
- 已入金订单取消返回409；不能撤销已入金的转账/红包。取消后的 fund 返回409“订单已取消，不再入金或发送”；如果与入金并发，只有一个事务成功，另一方返回409并查询原订单。403表示不是付款人/当前成员，401表示需要重新登录。
- Web 确认页及授权返回弹窗提供“取消订单 → 确认取消订单”，另有“保留订单”返回确认前状态。关闭弹窗/返回修改不等于取消服务器订单。取消响应丢失时先 GET 原订单核实，只有服务器 confirmed cancelled 才终结本地业务操作；不发送资金卡片。已取消订单不继续轮询，不再显示授权/入金按钮。
- App 应复用以上接口；不能把取消订单改成退款、撤回业务消息、仅本地删除或重新创建订单。

## 17. Android 浏览器授权与回到 App（2026-10-20）

- App 使用系统浏览器提供的 Custom Tabs，登录、两步验证和 YIDT 操作均由浏览器处理，不内嵌登录 WebView，也不读取浏览器 cookie。
- `GET /auth/native/login` 只接受单个 state、nonce、code_challenge、code_challenge_method=S256、redirect_uri；随机值43～128字符，challenge43字符；redirect_uri 固定为聊天域名 `/app/callback`。现有主站 OAuth 回调验证签名、uid、SID 后，303到 `/app/callback?code=...&state=...`。不向 URL 写入长期 token。
- `POST /auth/native/exchange` JSON 包含 code/state/nonce/code_verifier/固定 redirect_uri；拒绝浏览器 Origin、Sec-Fetch-Mode 和 yc_session。交换码60秒内一次性使用，绑定 PKCE、state、nonce、主站 UID/SID。返回 uid/user/mxid/device_id/token/device_proof/new_device/homeserver。恢复原设备须带同 UID 的 device_proof；不能单凭 device_id 打开旧密钥库。
- `GET /.well-known/assetlinks.json` 公开发布受信 APK 包名及签名证书 SHA-256，200 application/json，无重定向，缓存1小时；这里只含公钥证书指纹。HTTPS App Links 仅匹配 `/app/callback`、`/app/return`。修复版 `org.yitiao.yichat.mobile` 可与旧候选版并存，保护旧 App 的数据及密钥。
- `GET /api/wallet` 对原生设备返回 `bind_url=https://yidt.yitiao.org/services/yichat/?client=android`；绑定经过原登录/CSRF/PIN 校验后回固定 `/app/return`，只触发 App 查询绑定状态。网页原地址与返回保持原样。
- 原生订单使用 `/app/return?order={id}` 返回；YIDT 批准只带原订单引用，App 查询权威状态后仍须用户明确 fund。返回、查看、预览不扣款。取消沿用第16节。
- `POST/GET /api/native/rooms/{room}/transactions/{txn}` 为原生 SDK txn 登记/查询 attachments、business 引用。必须是本人原生设备及当前房间权限；附件仅本人在该房间有效上传，业务引用依原业务记录校验；不同元数据复用 txn 返回409。网关只添加最小外层引用，不改 SDK 密文，不存解密密钥，不重新执行资产操作。
- 附加表 native_logins/native_codes/native_devices/native_transactions 由幂等迁移创建；不删除原账号、设备、密钥、归档或资金记录。原生 SDK 与业务请求在网关按 UID 共享约6次/秒准入及429冷却，现有10次/秒最终限制保留。


## 18. 2026-10-21 正式版增量

群号/角色与管理、私聊自动加入和首条消息可见性、主站资料社交、撤回原始密文证明，见仓库根目录 PRODUCTION-20261021.md 的接口表及权限说明。红包最低本金按份数×0.01；份数不超过当前群成员数，入金时重查；发送者承担 (份数+1)×每笔 gas。

### 18.1 群头像与登录设备（2026-10-21）

群主在详情页选择/移除头像。`POST /api/groups/{room}/avatar` 接收最多2MB实际图片流，共用上传限速、并发和额度；在读正文前及上传过程中复核群主/成员/SID。PNG/JPEG/WebP 解码为去元数据的 256×256 PNG，返回 id；`POST /api/groups/{room}/manage` 的 `action:avatar,key,image:id|null` 以幂等操作发布原生 m.room.avatar。原生未获内容哈希授权的 avatar 写入均拒绝。`GET /api/groups/{room}/avatar/{id}` 仅当前成员读取当前头像，不提供旧图历史或私聊附件访问旁路。

Chat 不再提供设备 SAS 验证。主站 `/account/` 的登录设备管理通过带 CSRF 的 `POST /account/devices/revoke/` 撤销本人的指定主站 SID 或其他所有 SID。对应 YiChat 每次认证及同步长轮询返回前复核；前台约30秒检查，focus/联网时检查。退出仅撤销令牌/锁定本地数据，不删除 Matrix 设备、历史密钥或归档。

### 18.2 App 连续日记录核验（2026-10-21）

`POST /api/records/verify` 请求精确字段 `{room,start,end,days:[{day,count,sha256,complete}]}`；start/end 为 ISO UTC 连续1至5日。正文、私钥、完整文件及多余字段均拒绝。返回 `{format:"yichat-record-digests-v1",room,days:[{day,state,server_count,sha256,error}]}`，state 为 ok/mismatch/incomplete/not_sealed/forbidden/quarantined/unavailable。全日历史权限、封存状态及缺口规则与每日归档一致。

新原包核验摘要独立于两个不变的 ZIP 规格：排序键紧凑 UTF-8 JSON + LF 的头 `{day,format:"yichat-record-digests-v1",room_id,version:1}`，随后 sequence/event_id 排序的 `{event_id,received_ms,redacts,room_id,sender,sequence,sha256,type}`；sha256 是接受时规范化原事件包（含 E2EE 密文）的可信 SHA。只包含 m.room.message、m.room.encrypted、m.room.redaction，保留撤回前原包及撤回记录。整个字节流 SHA-256，空日仍包含日期头。根摘要由服务端存证生成，绑定 events-manifest ZIP SHA，在清理原实体前保留。旧实体已清理且无此摘要不允许从客户端回填，明确 unavailable。

`POST /api/records/verify-files` 请求 `{room,start,end,days:[{day,spec,bytes,sha256}]}`，spec 为两种既有归档规格，返回 `format:yichat-archive-v1` 与每日 state/sha256/bytes。仅传本地计算的文件摘要，服务端按全日权限与已封存根比对；实体已清理但可信根保留时仍可核验本地 ZIP。

App 在核验通过后独立只读显示对应原消息包；私聊先本地验证用户输入的 Matrix 恢复密钥，再恢复标准加密密钥备份并本地解密，不上传密钥或正文。文件模式支持同一会话连续1至5个统一每日 ZIP（单文件<=100MB，事件文本<=20MB），与内部原包模式分开。`.ycb` 口令快照不是统一每日 ZIP。本页不导入实时会话、不恢复/发送旧消息、不将普通实时消息标记成已校验。失败、缺包、无权限、未封存不展示为可信。退出清空页面中核验数据与明文。
