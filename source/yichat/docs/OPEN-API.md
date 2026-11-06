# YiChat 开发者 API

版本 `v1`，基础地址：`https://chat.yitiao.org/open/v1`

这套 API 让你用自己的账户，通过程序收发自己的 YiChat 消息：

- 查询会话列表（包括已归档的）和服务器保存的消息。转账、红包、站内分享等消息会转换成文字，图片和文件显示文件名。
- 下载消息中的图片和文件。
- 向私聊和群聊发送文字、图片和文件。
- 通过 WebSocket 实时接收新消息，可以订阅全部会话，也可以只订阅一个会话。

它不能修改会话设置、管理群组、撤回消息，也不能操作钱包或资金。

API 与 YiChat 网页版、Android 版、桌面版的客户端逻辑完全独立：客户端不使用这套 API，API Key 也不能用来访问客户端接口。

---

## 目录

1. [快速开始](#快速开始)
2. [API Key](#api-key)
3. [认证](#认证)
4. [限速](#限速)
5. [错误格式](#错误格式)
6. [读取](#读取)
   - [查询会话列表](#查询会话列表)
   - [查询会话消息](#查询会话消息)
   - [下载图片和文件](#下载图片和文件)
7. [发送](#发送)
   - [发送文字](#发送文字)
   - [发送图片或文件](#发送图片或文件)
   - [发送规则](#发送规则)
8. [消息对象](#消息对象)
   - [消息转换为文字的规则](#消息转换为文字的规则)
9. [WebSocket：订阅新消息](#websocket订阅新消息)
10. [数据范围与隐私](#数据范围与隐私)
11. [完整示例](#完整示例)
12. [常见问题](#常见问题)

---

## 快速开始

1. 在电脑或手机浏览器打开 <https://chat.yitiao.org> 并登录。
2. 点击左下角头像，选择 **开发者 API**，输入名称后点击 **创建 API Key**。
3. **立即复制 Key。** 它只显示这一次，关闭对话框后无法再次查看。
4. 调用接口：

```bash
export YICHAT_KEY='yck_…'   # 你的 Key
# 会话列表
curl -H "Authorization: Bearer $YICHAT_KEY" https://chat.yitiao.org/open/v1/conversations
# 向会话发送一句话（会话 ID 需要 URL 编码：! → %21，: → %3A）
curl -H "Authorization: Bearer $YICHAT_KEY" -H 'Content-Type: application/json' \
  -d '{"text":"你好"}' \
  https://chat.yitiao.org/open/v1/conversations/%21AbCdEf123%3Achat.yitiao.org/messages
```

---

## API Key

| 规则 | 说明 |
| --- | --- |
| 格式 | `yck_` 加 43 个字符（`A–Z a–z 0–9 _ -`），共 47 个字符 |
| 数量 | 每个账户最多 **3** 个。满额后需先删除一个才能创建 |
| 申请与管理 | **只能在网页版**（浏览器）中创建、查看列表和删除。Android 和桌面 App 中不提供，App 会话也不能调用管理接口 |
| 显示 | 完整 Key 只在创建时显示一次。服务器只保存它的 SHA-256 摘要，无法找回；丢失后请删除并重新创建 |
| 列表 | 列表中只显示名称、前 12 个字符（例如 `yck_AbCdEfGh…`）、创建时间和最近使用时间（约每分钟更新一次） |
| 有效期 | 长期有效，直到你删除它。与网页登录状态无关：退出网页登录不影响 Key，**发送消息也不需要你同时在网页或 App 中登录** |
| 失效 | 删除 Key、主站账户注销或不可用时，Key 立即失效；已建立的 WebSocket 会在 10 秒内收到 `closing` 并断开 |
| 权限 | 读取：与你本人在 YiChat 中能看到的内容相同。发送：与你本人在 App 中发送消息的规则相同（见[发送规则](#发送规则)） |

**Key 可以以你的名义发消息。** 请像保管密码一样保管它：不要提交到代码仓库，不要放在网页前端或 URL 中，不要发给他人。怀疑泄露时，立即在网页版中删除。

---

## 认证

每个请求（包括 WebSocket 握手）都在请求头中携带 Key：

```
Authorization: Bearer yck_…
```

- 只接受请求头，不接受 URL 参数或 Cookie。
- 网页或 App 的登录会话令牌不能用于 `/open/v1`；API Key 也不能用于网站的其他接口。
- 发送类请求（`POST`）不要带 `Origin` 请求头。浏览器跨站发起的请求会被拒绝。

---

## 限速

所有接口共用一个限制：

| 限制 | 规则 |
| --- | --- |
| 同一账户 | 每 **1 秒**最多 **5** 次，同一账户的所有 Key 合计 |

不按 IP 限速：同一 IP 下的不同账户各自计数。

- 计数采用精确的滑动 1 秒窗口：任意连续 1 秒内超过 5 次即被拒绝，没有突发额度，也不会累积。
- **计入次数的操作**：每个 REST 请求（读取、**发送、上传、下载**都算一次）、每次 WebSocket 连接尝试，以及客户端在 WebSocket 中发送的每一条消息。服务器推送给你的消息和 ping **不计数**。
- 一次上传或下载无论文件多大，都只计一次；上传另外受下面的流量限制。
- 认证失败的请求（Key 缺失、格式错误或已删除）直接返回 `401`，不计入任何账户的限额。
- 超限时返回 HTTP `429`，响应头 `Retry-After` 是建议等待的秒数，响应体中 `retry_after_ms` 是毫秒数：

```json
{"errcode":"M_LIMIT_EXCEEDED","error":"请求过于频繁，请稍后再试","code":"YICHAT_RATE_LIMITED","retry_after_ms":412}
```

**上传流量**：与你在网页版和 App 中上传共用同一套账户额度——单个文件最大 100000000 字节（100 MB），每天合计 1 GiB，上传速度约 500 KB/秒，最多 3 个同时进行的上传。超出时返回 `413` 或 `429`，见 `error` 说明。

建议：客户端每次请求间隔不少于 200 ms；收到 429 时按 `retry_after_ms` 等待后再重试。

---

## 错误格式

除 422 外，所有错误都返回下面格式的 JSON：

```json
{"errcode":"M_FORBIDDEN","error":"中文说明","code":"YICHAT_…"}
```

| HTTP | `code` | 含义 |
| --- | --- | --- |
| 400 | `YICHAT_RANGE_TOO_LONG` | 消息查询的时间范围超过 24 小时 |
| 400 | `YICHAT_REQUEST_REJECTED` | 参数不合法（见 `error`），例如文字为空或超过 2000 个字符、文件名不合法、文件为空 |
| 401 | `YICHAT_API_KEY_INVALID` | 缺少 Key、Key 格式错误、已删除，或所属账户不可用 |
| 403 | `YICHAT_NOT_MEMBER` | 你当前不是该会话的成员 |
| 403 | `YICHAT_LEGACY_ROOM` | 旧版加密会话已停用，不能发送 |
| 403 等 | `YICHAT_SEND_REJECTED` | 服务器按会话规则拒绝了这条消息（例如被禁言、对方未开放私聊），HTTP 状态码和 `error` 来自服务器 |
| 403 | `YICHAT_REQUEST_REJECTED` | 其他发送限制，例如对方已注销、群已解散，见 `error` |
| 404 | `YICHAT_REQUEST_REJECTED` | 附件不存在 |
| 410 | `YICHAT_REQUEST_REJECTED` | 附件已超过保存期 |
| 411 | `YICHAT_REQUEST_REJECTED` | 上传缺少 `Content-Length` |
| 413 | `YICHAT_REQUEST_REJECTED` | 文件超过 100 MB，或超过当日上传额度 |
| 415 | `YICHAT_IMAGE_TYPE` | 指定 `type=image`，但文件不是 PNG、JPEG、GIF、WebP 或 AVIF |
| 415 | `YICHAT_REQUEST_REJECTED` | 发送文字时 `Content-Type` 不是 `application/json` |
| 422 | — | 缺少必需的查询参数，或参数不是整数（例如没有 `start`）。此时响应体是框架的校验格式 `{"detail":[…]}`，不含 `code` 字段 |
| 429 | `YICHAT_RATE_LIMITED` | 超过限速 |
| 503 | `YICHAT_HISTORY_PENDING` | 服务器消息存证正在同步，请按 `Retry-After` 重试 |
| 503 | `YICHAT_API_DEVICE_RESET` | 这个 Key 使用的发送设备已在别处被登出，服务器已重置，**直接重试即可**（建议沿用原 `txn_id`） |

请以 HTTP 状态码和 `code` 字段判断错误类型；`error` 中的文字可能调整，不要依赖它。

---

## 读取

读取接口都是 `GET`，返回 `application/json; charset=utf-8`（下载文件除外）。时间均为 **Unix 毫秒时间戳**（UTC）。

### 查询会话列表

```
GET /open/v1/conversations?archived=include
```

返回你当前所在的所有会话（私聊和群聊），**默认包括已归档的会话**。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `archived` | `include` | `include`：全部；`exclude`：不含已归档；`only`：只含已归档 |

不包括：你已退出的会话、你删除（隐藏）且之后没有新消息的会话、已停用的会话，以及对方还未发送消息、尚未对你显示的私聊。

**响应**

```json
{
  "conversations": [
    {
      "id": "!AbCdEf123:chat.yitiao.org",
      "type": "direct",
      "name": "bob",
      "peer": {"mxid": "@u_…:chat.yitiao.org", "username": "bob", "deleted": false},
      "archived": false,
      "pinned": true,
      "muted": false,
      "encrypted": false,
      "last_message_at": 1793964467123
    },
    {
      "id": "!GhIjKl456:chat.yitiao.org",
      "type": "group",
      "name": "周末爬山",
      "group_number": "123456789",
      "archived": true,
      "pinned": false,
      "muted": false,
      "encrypted": false,
      "last_message_at": 1793960000000
    }
  ]
}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 会话 ID，用于查询和发送消息 |
| `type` | `direct`（私聊）或 `group`（群聊） |
| `name` | 私聊为对方用户名；群聊为群名称，暂时无法获取时为 `null` |
| `peer` | 仅私聊。`deleted` 为 `true` 表示对方账户已注销 |
| `group_number` | 仅群聊，9 位群号 |
| `archived` / `pinned` / `muted` | 你自己对此会话的设置：归档、置顶、免打扰 |
| `encrypted` | `true` 表示旧版端到端加密会话：服务器无法读取其中的正文，也不能再发送消息 |
| `last_message_at` | 最近一条消息的服务器接收时间，没有消息时为 `null` |

### 查询会话消息

```
GET /open/v1/conversations/{id}/messages?start=…&end=…&limit=100&after=…
```

按服务器接收顺序（从早到晚）返回 `[start, end)` 时间范围内服务器保存的消息。

| 参数 | 必需 | 说明 |
| --- | --- | --- |
| `{id}` | 是 | 会话 ID，需要 URL 编码（`!` → `%21`，`:` → `%3A`） |
| `start` | 是 | 起始时间（包含），毫秒 |
| `end` | 是 | 结束时间（不包含），毫秒。必须满足 `start < end`，且 **`end - start ≤ 86400000`（24 小时）** |
| `limit` | 否 | 每页条数，1～200，默认 100 |
| `after` | 否 | 翻页游标，填入上一页返回的 `next_after` |

- 时间按**服务器接收时间**（`received_at`）筛选。
- 只返回你作为成员期间的消息：加入会话之前、退出期间的消息不会返回。
- 你必须当前仍是该会话成员，否则返回 `403 YICHAT_NOT_MEMBER`。
- 需要更长的时间范围时，请按 24 小时分段查询。

**响应**

```json
{
  "conversation_id": "!AbCdEf123:chat.yitiao.org",
  "start": 1793878067000,
  "end": 1793964467000,
  "messages": [ { "id": "$evt1", "...": "见消息对象" } ],
  "next_after": "48213"
}
```

`next_after` 不为 `null` 时表示还有更多消息：保持 `start`、`end` 不变，带上 `after=<next_after>` 再请求一次，直到它为 `null`。

```bash
ID='%21AbCdEf123%3Achat.yitiao.org'
END=$(($(date +%s)*1000)); START=$((END-86400000))
curl -H "Authorization: Bearer $YICHAT_KEY" \
  "https://chat.yitiao.org/open/v1/conversations/$ID/messages?start=$START&end=$END&limit=200"
```

### 下载图片和文件

```
GET /open/v1/files/{attachment_id}
```

下载消息中的一张图片或一个文件。`attachment_id` 和完整的下载路径都在消息对象的 `attachment` 字段中（`attachment.download_url`）。

- **每次请求只下载一个文件**，计入一次限速。需要多个文件时逐个请求。
- 响应是文件的原始字节：`Content-Type: application/octet-stream`，`Content-Disposition: attachment`，`Content-Length` 为文件大小。文件名和原始类型请使用 `attachment.name` 和 `attachment.mimetype`。
- 你可以下载：自己上传的文件，以及你作为成员期间看到的消息中的文件。
- 文件按 YiChat 的 30 天规则保存，到期后返回 `410`。服务器正在同步时返回 `503`，按 `Retry-After` 重试。

```bash
curl -H "Authorization: Bearer $YICHAT_KEY" -o cat.png \
  https://chat.yitiao.org/open/v1/files/0123456789abcdef0123456789abcdef0123456789abcdef
```

---

## 发送

### 发送文字

```
POST /open/v1/conversations/{id}/messages
Content-Type: application/json

{"text": "晚上一起吃饭？", "txn_id": "dinner-0001"}
```

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `text` | 是 | 1～2000 个字符（Unicode 码点），不能全是空白 |
| `txn_id` | 否 | 幂等标识，1～64 个字母、数字或 `. _ -`。不填时服务器随机生成 |

**响应**

```json
{"event_id": "$Qx7…", "conversation_id": "!AbCdEf123:chat.yitiao.org", "txn_id": "dinner-0001"}
```

`event_id` 就是这条消息的 ID，之后会出现在查询结果和 WebSocket 推送中。

**幂等重试**：网络中断、不确定是否发送成功时，用**同一个 Key、同一个 `txn_id`** 重试，不会重复发送，返回的仍是第一次的 `event_id`。注意：同一个 `txn_id` 即使内容不同，也只会返回第一条消息；每条新消息请使用新的 `txn_id`。

文字中的网址按普通文字发送，接收方的 App 会照常显示链接。

### 发送图片或文件

```
POST /open/v1/conversations/{id}/files?name=cat.png&type=auto
Content-Type: image/png
Content-Length: 48213

<文件的原始字节>
```

上传文件并作为一条消息发送，一个请求完成。

| 参数 | 必需 | 说明 |
| --- | --- | --- |
| `name` | 是 | 文件名，1～200 个字符，不能包含控制字符、`/` 或 `\`。接收方看到的就是这个名字 |
| `type` | 否 | `auto`（默认）：PNG、JPEG、GIF、WebP、AVIF 作为图片发送，其他作为文件；`image`：必须是上述图片格式，否则返回 `415`；`file`：总是作为文件发送 |
| `txn_id` | 否 | 幂等标识，规则同上 |
| `Content-Type` 请求头 | 否 | 文件的 MIME 类型，例如 `image/jpeg`、`application/pdf`，默认 `application/octet-stream` |
| `Content-Length` 请求头 | 是 | 文件大小，1～100000000 字节。不支持分块上传（chunked） |

请求体就是文件本身，不要使用 `multipart/form-data`。

**响应**

```json
{
  "event_id": "$Qx8…",
  "conversation_id": "!AbCdEf123:chat.yitiao.org",
  "txn_id": "3f1c…",
  "kind": "image",
  "attachment": {
    "id": "0123456789abcdef0123456789abcdef0123456789abcdef",
    "name": "cat.png",
    "mimetype": "image/png",
    "size": 48213,
    "expires": 1796601600,
    "download_url": "/open/v1/files/0123456789abcdef0123456789abcdef0123456789abcdef"
  }
}
```

`expires` 是文件在服务器上的到期时间（Unix **秒**）；之后有人再引用它，到期时间会相应延长，规则与 App 相同。

```bash
ID='%21AbCdEf123%3Achat.yitiao.org'
# 图片
curl -H "Authorization: Bearer $YICHAT_KEY" -H 'Content-Type: image/jpeg' \
  --data-binary @photo.jpg \
  "https://chat.yitiao.org/open/v1/conversations/$ID/files?name=photo.jpg"
# 文件
curl -H "Authorization: Bearer $YICHAT_KEY" -H 'Content-Type: application/pdf' \
  --data-binary @report.pdf \
  "https://chat.yitiao.org/open/v1/conversations/$ID/files?name=report.pdf&type=file"
```

上传较大的文件需要一些时间（上传速度约 500 KB/秒），请把客户端超时设得足够长。上传完成但发送失败时（例如中途被禁言），文件不会出现在会话中，会在 30 天后清理。

### 发送规则

API 发送的消息与你在 App 中发送的消息完全相同，接收方看到的就是你本人发的消息：

- 你必须当前是该会话的成员；私聊和群聊都可以发送。
- 旧版加密会话（会话列表中 `encrypted: true`）已停用，不能发送。
- 其他规则与 App 相同：例如在群里被禁言、群已解散、对方账户已注销、对方的隐私设置不允许时，都会被拒绝。
- 每个 Key 第一次发送时，服务器会为它登录一个专用设备 **“YiChat API · Key 名称”**，你可以在设备列表中看到它。删除 Key 时这个设备会被登出。
- 发送**不需要**你同时在网页或 App 中保持登录。服务器只为每条已验证的消息单独授权一次，授权 2 分钟内有效、只能使用一次。

---

## 消息对象

REST 查询和 WebSocket 推送返回的消息格式相同：

```json
{
  "id": "$Qx7…",
  "conversation_id": "!AbCdEf123:chat.yitiao.org",
  "sequence": "48213",
  "sender": {"mxid": "@u_…:chat.yitiao.org", "username": "bob"},
  "sent_at": 1793964467000,
  "received_at": 1793964467123,
  "kind": "image",
  "text": "[图片] cat.png",
  "attachment": {
    "id": "0123456789abcdef0123456789abcdef0123456789abcdef",
    "name": "cat.png",
    "mimetype": "image/png",
    "size": 48213,
    "download_url": "/open/v1/files/0123456789abcdef0123456789abcdef0123456789abcdef"
  },
  "share": null,
  "msgtype": "m.image",
  "content": {"msgtype": "m.image", "body": "cat.png", "...": "…"},
  "encrypted": false,
  "redacted": false
}
```

| 字段 | 说明 |
| --- | --- |
| `id` | 消息 ID（Matrix 事件 ID），全局唯一 |
| `sequence` | 服务器接收序号（字符串形式的整数），全局递增，可用于排序和去重 |
| `sender.username` | 发送者用户名。账户已注销或无法识别时为 `null` |
| `sent_at` | 发送方客户端记录的时间 |
| `received_at` | 服务器接收时间，查询按此字段筛选 |
| `kind` | 消息种类，见下表。**推荐用它和 `text` 处理消息** |
| `text` | 消息的文字形式：普通消息是正文；转账、红包、分享、图片、文件等由服务器转换为文字（见下文规则）。加密或已撤回时为 `null` |
| `attachment` | 图片、文件、语音、视频的附件信息，其他消息为 `null`。`mimetype`、`size` 来自发送方，可能为 `null` |
| `share` | 转账、红包、收款和站内分享的详细信息，其他消息为 `null` |
| `msgtype` | 原始消息类型，例如 `m.text`、`m.image`、`m.file` |
| `content` | 原始消息内容（Matrix `content`），供需要更多细节时使用 |
| `encrypted` | `true`：旧版端到端加密消息，`kind` 为 `encrypted`，`text`、`content` 等为 `null` |
| `redacted` | `true`：消息已被撤回，`kind` 为 `redacted`，`text`、`content` 等为 `null` |

`content`、`share` 中的字段可能随功能更新而增加，`kind` 也可能增加新的取值。请忽略你不认识的字段和取值，不要因此报错。

### 消息转换为文字的规则

转账、红包、站内分享在 YiChat 中本质上是网址，服务器会把它们转换成文字：

| `kind` | `text` 示例 | 说明 |
| --- | --- | --- |
| `text` | `晚上一起吃饭？` | 普通文字，原样返回；其中的外部网址保持不变 |
| `transfer` | `[转账] 0.01 YIDT · 房租` | 私聊转账。备注为默认的“私聊转账”时不显示 |
| `red_packet` | `[红包] 1 YIDT · 3 个 · 恭喜发财` | 群红包：总金额、份数、标题 |
| `receipt` | `[已收款] 0.01 YIDT` | 收款确认 |
| `gift` | `[赠卡]` | 赠送卡片 |
| `share` | `看这笔 [分享] YIDT 交易 吧` | YCards 卡片、YIDT 交易、斗地主房间、斗地主对局记录、跳伞记录等站内分享。分享网址被替换为 `[分享] 标题`，其余文字保留 |
| `image` | `[图片] cat.png` | 图片，附件信息在 `attachment` 中 |
| `file` | `[文件] report.pdf` | 文件 |
| `audio` | `[语音] 语音.ogg` | 语音 |
| `video` | `[视频] clip.mp4` | 视频 |
| `encrypted` | `null` | 旧版加密消息 |
| `redacted` | `null` | 已撤回 |

转账、红包、收款的 `share`：

```json
{"type": "order", "order_id": "AbCd…", "url": "https://chat.yitiao.org/orders/AbCd…",
 "amount": "0.01", "currency": "YIDT", "state": "awaiting_receipt"}
```

- 金额是十进制字符串，单位 YIDT。`state` 是订单当前状态，查询时实时读取：`awaiting_deposit`（待付款）、`awaiting_receipt`（转账待收款）、`active`（红包可领取）、`paid`（已完成）、`refunding` / `refunded`（到期退款中 / 已退款）、`cancelled`（已取消）。
- 只有属于**同一会话**的订单才会显示金额和状态；找不到时 `text` 为 `[转账 / 红包]`（收款为 `[已收款]`），`share` 中只有 `type`、`order_id` 和 `url`。
- 本 API 不能领取、确认或取消转账和红包，请在 App 中操作。

站内分享的 `share`：`{"type": "card", "url": "…", "title": "YCards · 星空"}`，`type` 为 `card`、`transaction`、`doudizhu`、`doudizhu_record` 或 `game`。

---

## WebSocket：订阅新消息

```
wss://chat.yitiao.org/open/v1/stream                       # 全部会话
wss://chat.yitiao.org/open/v1/stream?conversation=<会话ID>   # 只订阅一个会话
Authorization: Bearer yck_…
```

实时接收新消息，包括你自己从任何设备（包括 API）发送的消息。

| 参数 | 说明 |
| --- | --- |
| `conversation` | 可选。只接收这一个会话（私聊或群聊）的消息，值为会话 ID（需要 URL 编码）。你必须当前是该会话的成员，否则以 `4403` 关闭；订阅期间你退出该会话，也会以 `4403` 关闭 |

- 握手时必须在请求头中带 `Authorization`。浏览器的 `WebSocket` API 不能设置请求头，所以请在服务器端程序中使用，例如 Python `websockets`、Node.js `ws`、Go，或命令行工具 `websocat`。
- **每个账户最多同时运行 3 个订阅**，不论使用哪个 Key：例如可以用同一个 Key 分别订阅 3 个私聊，或者 1 个订阅全部、2 个各订阅一个会话。第 4 个连接会以 `4409` 关闭。连接断开后名额立即释放。
- 连接只推送**建立连接之后**到达的消息。断线期间的消息请用 REST 接口按时间补齐（见下文“断线重连”）。

### 服务器发送的消息

所有消息都是 JSON 文本帧，用 `type` 区分：

| `type` | 示例 | 说明 |
| --- | --- | --- |
| `ready` | `{"type":"ready","server_time":1793964467000,"ping_interval":30,"conversation_id":null}` | 连接成功后的第一条消息。`conversation_id` 为订阅的会话，订阅全部时为 `null` |
| `message` | `{"type":"message","conversation_id":"!…","message":{…}}` | 新消息，`message` 的格式见[消息对象](#消息对象) |
| `redaction` | `{"type":"redaction","conversation_id":"!…","message_id":"$…","sequence":"48214"}` | 某条消息被撤回 |
| `ping` | `{"type":"ping","server_time":1793964497000}` | 保活。**超过 30 秒没有其他消息时发送**，因此即使没有新消息，也保证至少每 1 分钟一次 |
| `pong` | `{"type":"pong","server_time":…}` | 回复客户端发送的 `ping` |
| `closing` | `{"type":"closing","code":"YICHAT_STREAM_LIMIT","error":"…"}` | 服务器即将关闭连接，随后发送关闭帧 |

请忽略不认识的 `type`，以便兼容将来增加的消息类型。

### 客户端可以发送的消息

不需要发送任何消息，也能一直收到推送。如果需要，可以发送 `{"type":"ping"}`，服务器会回复 `pong`。

- 客户端发送的每条消息都计入账户限速（5 次/秒）。
- 单条消息不超过 4096 字节，否则连接会以 `1009` 关闭。
- WebSocket 只用于接收。发送聊天消息请使用 REST 接口；其他内容会被忽略。

### 关闭码

| 关闭码 | `closing.code` | 含义 | 建议处理 |
| --- | --- | --- | --- |
| `1000` / `1001` | — | 正常关闭或服务器重启 | 等待 1～5 秒后重连 |
| `1009` | — | 客户端消息过大 | 修正程序 |
| `1011` | — | 服务器内部错误 | 按指数退避重连，并用 REST 接口补齐消息 |
| `4401` | `YICHAT_API_KEY_INVALID` | Key 无效、已删除，或账户不可用 | 停止重连，检查 Key |
| `4403` | `YICHAT_NOT_MEMBER` | 指定的会话不存在，或你不是/已不是它的成员 | 停止重连，检查会话 ID |
| `4409` | `YICHAT_STREAM_LIMIT` | 账户已有 3 个订阅在运行 | 关闭不用的连接后再连接 |
| `4429` | `YICHAT_RATE_LIMITED` | 超过账户限速（连接时或连接中） | 等待 1 秒以上再重连 |

### 心跳与超时

如果超过 **75 秒**没有收到任何消息（包括 `ping`），可以认为连接已失效，主动断开并重连。

### 断线重连

1. 记录最后收到的消息的 `received_at`（也可以同时记录 `id` 用于去重）。
2. 重连时按指数退避等待：1 秒、2 秒、4 秒……最长 60 秒，并加入随机抖动。
3. 收到 `ready` 后，用 REST 接口查询每个会话 `[最后的 received_at, 当前时间)` 范围内的消息，按 `id` 去重。超过 24 小时需要分段查询。

---

## 数据范围与隐私

- **只能读取服务器上保存的消息**：附件和消息正文按 YiChat 的 30 天规则保存，超过保存期的消息查询不到，文件也无法下载。
- **成员期间**：只能看到你作为成员期间的消息和文件，与在 App 中能看到的内容一致。
- **旧版加密会话**：服务器无法读取正文，只返回 `encrypted: true`，也不能发送。
- **撤回**：已撤回的消息只返回 `redacted: true`，不返回内容。本 API 不能撤回消息。
- **已读状态**：读取不会产生已读回执、不改变未读数，也不会触发“正在输入”。
- **设备**：只读取时不会出现在设备列表中；第一次发送后，会出现名为“YiChat API · Key 名称”的设备，删除 Key 时自动登出。

---

## 完整示例

### Python（websockets ≥ 13，httpx）

```python
import asyncio, json, os, random, time, uuid
from urllib.parse import quote
import httpx, websockets

KEY = os.environ['YICHAT_KEY']
BASE = 'https://chat.yitiao.org/open/v1'
HEAD = {'Authorization': f'Bearer {KEY}'}

def call(method, path, **kwargs):
    """带限速重试的请求。所有接口共用每秒 5 次的账户限额。"""
    while True:
        r = httpx.request(method, BASE + path, headers={**HEAD, **kwargs.pop('headers', {})}, timeout=600, **kwargs)
        if r.status_code == 429 or (r.status_code == 503 and r.headers.get('Retry-After')):
            time.sleep(max(1, int(r.headers.get('Retry-After', '1'))))
            continue
        r.raise_for_status()
        time.sleep(0.25)          # 不超过 5 次/秒
        return r

def room_path(room):
    return '/conversations/' + quote(room, safe='')

def messages(room, start, end):
    """读取 [start, end) 内的全部消息，自动按 24 小时分段并翻页。"""
    day = 86_400_000
    for s in range(start, end, day):
        e, after = min(s + day, end), ''
        while True:
            page = call('GET', room_path(room) + '/messages',
                        params={'start': s, 'end': e, 'limit': 200, 'after': after}).json()
            yield from page['messages']
            if not page['next_after']:
                break
            after = page['next_after']

def send_text(room, text):
    txn = uuid.uuid4().hex      # 重试时沿用同一个 txn_id，不会重复发送
    return call('POST', room_path(room) + '/messages', json={'text': text, 'txn_id': txn}).json()

def send_file(room, path, mimetype):
    with open(path, 'rb') as f:
        data = f.read()
    return call('POST', room_path(room) + '/files',
                params={'name': os.path.basename(path), 'txn_id': uuid.uuid4().hex},
                headers={'Content-Type': mimetype}, content=data).json()

def download(message, folder='.'):
    a = message['attachment']
    data = call('GET', '/files/' + a['id']).content
    with open(os.path.join(folder, os.path.basename(a['name'])), 'wb') as f:
        f.write(data)

async def stream(room=None):
    url = 'wss://chat.yitiao.org/open/v1/stream' + (f'?conversation={quote(room, safe="")}' if room else '')
    delay = 1
    while True:
        try:
            async with websockets.connect(url, additional_headers=HEAD) as ws:
                delay = 1
                async for frame in ws:
                    event = json.loads(frame)
                    if event['type'] == 'message':
                        m = event['message']
                        print(m['sender']['username'], ':', m['text'])
                    elif event['type'] == 'closing':
                        print('server closing:', event['code'])
        except websockets.ConnectionClosed as e:
            if e.rcvd and e.rcvd.code in (4401, 4403):
                raise SystemExit(f'停止重连：{e.rcvd.code}')
        except OSError:
            pass
        await asyncio.sleep(delay + random.random())
        delay = min(delay * 2, 60)

if __name__ == '__main__':
    convs = call('GET', '/conversations').json()['conversations']
    for c in convs:
        print(c['type'], c['name'], c['archived'])
    asyncio.run(stream())
```

### 一个简单的自动回复机器人（Python）

只订阅一个私聊，对方发来“ping”时回复“pong”：

```python
async def bot(room):
    url = f'wss://chat.yitiao.org/open/v1/stream?conversation={quote(room, safe="")}'
    me = None
    async with websockets.connect(url, additional_headers=HEAD) as ws:
        async for frame in ws:
            event = json.loads(frame)
            if event['type'] != 'message':
                continue
            m = event['message']
            if m['kind'] == 'text' and m['text'].strip() == 'ping' and m['sender']['username'] != me:
                await asyncio.to_thread(send_text, room, 'pong')
```

把 `me` 设为你自己的用户名，避免回复自己发送的消息（WebSocket 也会推送你自己发的消息）。

### Node.js（ws）

```js
import WebSocket from 'ws';
const room = encodeURIComponent('!AbCdEf123:chat.yitiao.org');
const ws = new WebSocket(`wss://chat.yitiao.org/open/v1/stream?conversation=${room}`, {
  headers: { Authorization: `Bearer ${process.env.YICHAT_KEY}` },
});
ws.on('message', (data) => {
  const event = JSON.parse(data);
  if (event.type === 'message') console.log(event.message.sender.username, event.message.text);
});
ws.on('close', (code) => console.log('closed', code));

// 发送文字
await fetch(`https://chat.yitiao.org/open/v1/conversations/${room}/messages`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${process.env.YICHAT_KEY}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: '来自 Node.js', txn_id: crypto.randomUUID() }),
});
```

### 命令行（websocat）

```bash
websocat -H "Authorization: Bearer $YICHAT_KEY" wss://chat.yitiao.org/open/v1/stream
websocat -H "Authorization: Bearer $YICHAT_KEY" \
  'wss://chat.yitiao.org/open/v1/stream?conversation=%21AbCdEf123%3Achat.yitiao.org'
```

---

## 常见问题

**能发送消息吗？** 可以。私聊和群聊都可以发送文字、图片和文件，见[发送](#发送)。不能撤回、编辑消息，也不能发起转账或红包。

**为什么转账、红包显示成 `[转账] 0.01 YIDT` 这样的文字？** 这些消息在 YiChat 中本质上是网址，服务器把它们转换成了容易处理的文字。详细信息在 `share` 字段中，原始内容在 `content` 中。

**图片和文件怎么下载？** 消息对象的 `attachment.download_url` 就是下载地址，每次请求下载一个文件。

**为什么一次只能查询 24 小时？** 这样可以限制单次查询的数据量。需要更长范围时按天分段查询，示例中的 `messages()` 已经实现了分段和翻页。

**为什么 WebSocket 连接返回 `4409`？** 你的账户已经有 3 个订阅在运行（例如上次的进程还没退出）。关闭不用的连接后再试。

**为什么返回 `4403`？** 你用 `conversation` 指定的会话不存在，或者你不是这个会话的成员。

**发送后接收方能看出是 API 发的吗？** 不能。消息与你在 App 中发送的一样；只有你自己能在设备列表中看到“YiChat API · Key 名称”设备。

**能在浏览器网页里直接使用吗？** 不建议。浏览器的 WebSocket 无法设置 `Authorization` 请求头，跨站请求也会被拒绝，而且把 Key 放进网页前端会泄露给访问者。请在服务器端或本地程序中使用。

**Key 丢了怎么办？** 无法找回。请在网页版中删除旧 Key，再创建一个新的。

**在 App 中能申请 Key 吗？** 不能。API Key 只能在网页版申请和管理。
