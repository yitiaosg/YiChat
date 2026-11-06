# YiChat 开发者 API

版本 `v1`，基础地址：`https://chat.yitiao.org/open/v1`

这套 API 用你自己的账户读取你自己的 YiChat 数据：会话列表（包括已归档的）、服务器保存的消息，以及通过 WebSocket 实时接收新消息。它是**只读**的：不能发送消息、修改会话、管理群组，也不能操作钱包或资金。

API 与 YiChat 网页版、Android 版、桌面版的客户端逻辑完全独立。客户端不使用这套 API；API Key 也不能用来访问客户端接口。

---

## 目录

1. [快速开始](#快速开始)
2. [API Key](#api-key)
3. [认证](#认证)
4. [限速](#限速)
5. [错误格式](#错误格式)
6. [REST 接口](#rest-接口)
   - [查询会话列表](#查询会话列表)
   - [查询会话消息](#查询会话消息)
7. [消息对象](#消息对象)
8. [WebSocket：订阅新消息](#websocket订阅新消息)
9. [数据范围与隐私](#数据范围与隐私)
10. [完整示例](#完整示例)
11. [常见问题](#常见问题)

---

## 快速开始

1. 在电脑或手机浏览器打开 <https://chat.yitiao.org> 并登录。
2. 点击左下角头像，选择 **开发者 API**，输入名称后点击 **创建 API Key**。
3. **立即复制 Key。** 它只显示这一次，关闭对话框后无法再次查看。
4. 调用接口：

```bash
export YICHAT_KEY='yck_…'   # 你的 Key
curl -H "Authorization: Bearer $YICHAT_KEY" https://chat.yitiao.org/open/v1/conversations
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
| 有效期 | 长期有效，直到你删除它。与网页登录状态无关：退出网页登录不影响 Key |
| 失效 | 删除 Key、主站账户注销或不可用时，Key 立即失效；已建立的 WebSocket 会在 10 秒内收到 `closing` 并断开 |
| 权限 | 与账户本人在 YiChat 中能看到的内容相同，并且只读 |

请把 Key 当作密码保管：不要提交到代码仓库，不要放在网页前端或 URL 中，不要发给他人。怀疑泄露时，立即在网页版中删除。

---

## 认证

每个请求（包括 WebSocket 握手）都在请求头中携带 Key：

```
Authorization: Bearer yck_…
```

- 只接受请求头，不接受 URL 参数或 Cookie。
- 网页或 App 的登录会话令牌不能用于 `/open/v1`；API Key 也不能用于网站的其他接口。

---

## 限速

REST 和 WebSocket 共用一个限制：

| 限制 | 规则 |
| --- | --- |
| 同一账户 | 每 **1 秒**最多 **5** 次，同一账户的所有 Key 合计 |

不按 IP 限速：同一 IP 下的不同账户各自计数。

- 计数采用精确的滑动 1 秒窗口：任意连续 1 秒内超过 5 次即被拒绝，没有突发额度，也不会累积。
- **计入次数的操作**：每个 REST 请求、每次 WebSocket 连接尝试，以及客户端在 WebSocket 中发送的每一条消息。服务器推送给你的消息和 ping **不计数**。
- 认证失败的请求（Key 缺失、格式错误或已删除）直接返回 `401`，不计入任何账户的限额。
- 超限时返回 HTTP `429`，响应头 `Retry-After` 是建议等待的秒数，响应体中 `retry_after_ms` 是毫秒数：

```json
{"errcode":"M_LIMIT_EXCEEDED","error":"请求过于频繁，请稍后再试","code":"YICHAT_RATE_LIMITED","retry_after_ms":412}
```

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
| 400 | `YICHAT_REQUEST_REJECTED` | 参数不合法（见 `error`） |
| 401 | `YICHAT_API_KEY_INVALID` | 缺少 Key、Key 格式错误、已删除，或所属账户不可用 |
| 403 | `YICHAT_NOT_MEMBER` | 你当前不是该会话的成员 |
| 422 | — | 缺少必需的查询参数，或参数不是整数（例如没有 `start`）。此时响应体是框架的校验格式 `{"detail":[…]}`，不含 `code` 字段 |
| 429 | `YICHAT_RATE_LIMITED` | 超过限速 |
| 503 | `YICHAT_HISTORY_PENDING` | 服务器消息存证正在同步，请按 `Retry-After` 重试 |

请以 HTTP 状态码和 `code` 字段判断错误类型；`error` 中的文字可能调整，不要依赖它。

---

## REST 接口

所有接口都是 `GET`，返回 `application/json; charset=utf-8`。时间均为 **Unix 毫秒时间戳**（UTC）。

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
| `id` | 会话 ID，用于查询消息 |
| `type` | `direct`（私聊）或 `group`（群聊） |
| `name` | 私聊为对方用户名；群聊为群名称，暂时无法获取时为 `null` |
| `peer` | 仅私聊。`deleted` 为 `true` 表示对方账户已注销 |
| `group_number` | 仅群聊，9 位群号 |
| `archived` / `pinned` / `muted` | 你自己对此会话的设置：归档、置顶、免打扰 |
| `encrypted` | `true` 表示旧版端到端加密会话，服务器无法读取其中的正文 |
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

---

## 消息对象

REST 和 WebSocket 返回的消息格式相同：

```json
{
  "id": "$Qx7…",
  "conversation_id": "!AbCdEf123:chat.yitiao.org",
  "sequence": "48213",
  "sender": {"mxid": "@u_…:chat.yitiao.org", "username": "bob"},
  "sent_at": 1793964467000,
  "received_at": 1793964467123,
  "msgtype": "m.text",
  "text": "晚上一起吃饭？",
  "content": {"msgtype": "m.text", "body": "晚上一起吃饭？"},
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
| `msgtype` | 消息类型，例如 `m.text`、`m.image`、`m.file`、`m.audio`、`m.video` |
| `text` | 正文或附件说明文字；没有时为 `null` |
| `content` | 原始消息内容（Matrix `content`），包括转账、红包、卡片、分享等业务字段。媒体只有引用，没有文件内容 |
| `encrypted` | `true`：旧版端到端加密消息，`msgtype`、`text`、`content` 都为 `null` |
| `redacted` | `true`：消息已被撤回，`msgtype`、`text`、`content` 都为 `null` |

`content` 中的字段可能随功能更新而增加。请忽略你不认识的字段，不要因为出现未知字段而报错。

---

## WebSocket：订阅新消息

```
wss://chat.yitiao.org/open/v1/stream
Authorization: Bearer yck_…
```

实时接收你所在会话中的新消息，包括你自己从任何设备发送的消息。

- 握手时必须在请求头中带 `Authorization`。浏览器的 `WebSocket` API 不能设置请求头，所以请在服务器端程序中使用，例如 Python `websockets`、Node.js `ws`、Go，或命令行工具 `websocat`。
- 每个 Key 同时只能有 **1** 个连接；每个账户最多 3 个 Key，因此最多 3 个连接。
- 连接只推送**建立连接之后**到达的消息。断线期间的消息请用 REST 接口按时间补齐（见下文“断线重连”）。

### 服务器发送的消息

所有消息都是 JSON 文本帧，用 `type` 区分：

| `type` | 示例 | 说明 |
| --- | --- | --- |
| `ready` | `{"type":"ready","server_time":1793964467000,"ping_interval":30}` | 连接成功后的第一条消息 |
| `message` | `{"type":"message","conversation_id":"!…","message":{…}}` | 新消息，`message` 的格式见[消息对象](#消息对象) |
| `redaction` | `{"type":"redaction","conversation_id":"!…","message_id":"$…","sequence":"48214"}` | 某条消息被撤回 |
| `ping` | `{"type":"ping","server_time":1793964497000}` | 保活。**超过 30 秒没有其他消息时发送**，因此即使没有新消息，也保证至少每 1 分钟一次 |
| `pong` | `{"type":"pong","server_time":…}` | 回复客户端发送的 `ping` |
| `closing` | `{"type":"closing","code":"YICHAT_API_KEY_INVALID","error":"…"}` | 服务器即将关闭连接，随后发送关闭帧 |

请忽略不认识的 `type`，以便兼容将来增加的消息类型。

### 客户端可以发送的消息

不需要发送任何消息，也能一直收到推送。如果需要，可以发送 `{"type":"ping"}`，服务器会回复 `pong`。

- 客户端发送的每条消息都计入账户限速（5 次/秒）。
- 单条消息不超过 4096 字节，否则连接会以 `1009` 关闭。
- 其他内容会被忽略。

### 关闭码

| 关闭码 | 含义 | 建议处理 |
| --- | --- | --- |
| `1000` / `1001` | 正常关闭或服务器重启 | 等待 1～5 秒后重连 |
| `1009` | 客户端消息过大 | 修正程序 |
| `1011` | 服务器内部错误 | 按指数退避重连，并用 REST 接口补齐消息 |
| `4401` | Key 无效、已删除，或账户不可用 | 停止重连，检查 Key |
| `4409` | 此 Key 已有一个连接 | 关闭旧连接，或为另一个程序创建新 Key |
| `4429` | 超过账户限速（连接时或连接中） | 等待 1 秒以上再重连 |

### 心跳与超时

如果超过 **75 秒**没有收到任何消息（包括 `ping`），可以认为连接已失效，主动断开并重连。

### 断线重连

1. 记录最后收到的消息的 `received_at`（也可以同时记录 `id` 用于去重）。
2. 重连时按指数退避等待：1 秒、2 秒、4 秒……最长 60 秒，并加入随机抖动。
3. 收到 `ready` 后，用 REST 接口查询每个会话 `[最后的 received_at, 当前时间)` 范围内的消息，按 `id` 去重。超过 24 小时需要分段查询。

---

## 数据范围与隐私

- **只能读取服务器上保存的消息**：附件和消息正文按 YiChat 的 30 天规则保存，超过保存期的消息查询不到。
- **成员期间**：只能看到你作为成员期间的消息，与在 App 中能看到的内容一致。
- **旧版加密会话**：服务器无法读取正文，只返回 `encrypted: true`。
- **撤回**：已撤回的消息只返回 `redacted: true`，不返回内容。
- **媒体文件**：`content` 中只有附件引用（例如 `url`），本 API 不提供文件下载。
- **只读**：不会产生已读回执、不改变未读数，也不会触发对方的“正在输入”或已读状态。
- API 访问不计入网页版或 App 的会话，也不会出现在你的设备列表中。

---

## 完整示例

### Python（websockets ≥ 13，httpx）

```python
import asyncio, json, os, random, time
import httpx, websockets

KEY = os.environ['YICHAT_KEY']
BASE = 'https://chat.yitiao.org/open/v1'
HEAD = {'Authorization': f'Bearer {KEY}'}

def get(path, **params):
    while True:
        r = httpx.get(BASE + path, headers=HEAD, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(r.json().get('retry_after_ms', 1000) / 1000)
            continue
        r.raise_for_status()
        time.sleep(0.25)          # 不超过 5 次/秒
        return r.json()

def messages(room, start, end):
    """读取 [start, end) 内的全部消息，自动按 24 小时分段并翻页。"""
    day = 86_400_000
    for s in range(start, end, day):
        e, after = min(s + day, end), ''
        while True:
            page = get(f'/conversations/{room}/messages', start=s, end=e, limit=200, after=after)
            yield from page['messages']
            if not page['next_after']:
                break
            after = page['next_after']

async def stream():
    delay = 1
    while True:
        try:
            async with websockets.connect('wss://chat.yitiao.org/open/v1/stream',
                                          additional_headers=HEAD) as ws:
                delay = 1
                async for frame in ws:
                    event = json.loads(frame)
                    if event['type'] == 'message':
                        m = event['message']
                        print(m['sender']['username'], ':', m['text'])
                    elif event['type'] == 'closing':
                        print('server closing:', event['code'])
        except websockets.ConnectionClosed as e:
            if e.rcvd and e.rcvd.code == 4401:
                raise SystemExit('API Key 无效或已删除')
        except OSError:
            pass
        await asyncio.sleep(delay + random.random())
        delay = min(delay * 2, 60)

if __name__ == '__main__':
    for c in get('/conversations')['conversations']:
        print(c['type'], c['name'], c['archived'])
    asyncio.run(stream())
```

### Node.js（ws）

```js
import WebSocket from 'ws';
const ws = new WebSocket('wss://chat.yitiao.org/open/v1/stream', {
  headers: { Authorization: `Bearer ${process.env.YICHAT_KEY}` },
});
ws.on('message', (data) => {
  const event = JSON.parse(data);
  if (event.type === 'message') console.log(event.message.sender.username, event.message.text);
});
ws.on('close', (code) => console.log('closed', code));
```

### 命令行（websocat）

```bash
websocat -H "Authorization: Bearer $YICHAT_KEY" wss://chat.yitiao.org/open/v1/stream
```

---

## 常见问题

**能用这套 API 发送消息吗？** 不能。v1 只读。

**为什么一次只能查询 24 小时？** 这样可以限制单次查询的数据量。需要更长范围时按天分段查询，示例中的 `messages()` 已经实现了分段和翻页。

**为什么 WebSocket 连接返回 `4409`？** 同一个 Key 已经有一个连接（例如上次的进程还没退出）。关闭旧连接，或者为另一个程序单独创建一个 Key。

**能在浏览器网页里直接使用吗？** 不建议。浏览器的 WebSocket 无法设置 `Authorization` 请求头，而且把 Key 放进网页前端会泄露给访问者。请在服务器端或本地程序中使用。

**Key 丢了怎么办？** 无法找回。请在网页版中删除旧 Key，再创建一个新的。

**在 App 中能申请 Key 吗？** 不能。API Key 只能在网页版申请和管理。
