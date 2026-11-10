# 原始消息、离线数据包和 GMT 整日校验 v2

原始材料独立存为私有 JSON 文件，图片/文件独立存为二进制文件。聊天事件携带 `org.yitiao.material` 描述，不依赖再次访问分享 URL。粘贴白名单站内 URL 或点击“分享到 YiChat”均请求服务器发布消息；原生客户端不能直接发送材料描述。卡片、赠卡、游戏战绩、转账/收款/红包和合并记录保存发送时的原始内容。赠卡在资产转移的同一事务内保存卡面，随后由可恢复的服务器队列发送。售出、拍卖和销毁不影响已经保存的原件。

设备先显示本地原件，联网仅比较摘要；内容未变只返回摘要元数据。内容损坏时下载原件，设备重新计算并确认；照片自动下载，普通文件只保存文件 ID、字节数和原摘要，点击下载后才保存实体；已下载附件按实际字节校验，未下载附件使用保存的摘要参加校验。合并记录也保存头像图像字节，不依赖头像原 URL；前端不显示原始摘要文本。App 在前台和后台主动保存原件，不要求先打开气泡；冷启动可从独立的加密消息数据库重建会话，即使 WebView/SDK 缓存为空。

## 保留和不可撤回

以服务器接受时间作为分享时间。普通材料五分钟后生成最终单条摘要，转账/收款/红包和赠卡不可撤回时立即生成。七天后清理原始消息、材料文件及单条校验摘要，保留整日摘要。附件在最后一条有效引用七天后清理；重复发送复用文件 ID，原件 JSON 的相同内容复用磁盘实体。旧版 URL 特殊消息通过一次性迁移删除；普通图片/文件、用户资金账本不属于该迁移范围。

日期始终是 GMT 的 `YYYY-MM-DD`，不使用客户端时区。次日 00:05，在源消息入库存证水位覆盖冻结边界后封存前一天。整日没有消息也生成空日摘要。只纳入消息和撤回事件，完整日期权限依据历史入群区间判断。摘要不接受客户端声明替代原始字节。

## 规范摘要

JSON 规范化为键名字典序、UTF-8、无额外空格，末尾一个 LF。原始事件和原件 JSON 保存其原始规范字符串，跨语言禁止解析后重序列化这些字符串再计算摘要。投影中的时间、序号、版本和附件大小使用十进制字符串，避免 Python/JS/Kotlin 浮点格式差异。

单条材料投影为 `format=yichat-material-v2, version="2", event_id, room_id, sender, shared_at, payload_sha256, files`；附件按 ID 排序，每项为 `id,bytes,sha256`。对规范投影计算 SHA-256。

整日投影为 `format=yichat-day-packet-v2,version="2",room_id,day,immutable_at,entries`。事件按服务器序号严格递增；每项含 `event_id,room_id,sender,type,sequence,received_ms,redacted,raw_sha256,material_sha256,files`。撤回状态取当日冻结边界前已接受的撤回；原件及附件使用已保存摘要，校验上传包时重新计算实际原件；携带的附件字节必须验证与其描述一致，未下载附件允许省略 data，使用原保存的摘要。摘要通过只说明声明与存证匹配，不代表本机持有附件实体。

Python、TypeScript 和 Kotlin 的共用测试向量在 `tests/fixtures/protocol-v2-vectors.json`；包含 Unicode、原始 `1.0`、空日、附件和原件时间绑定。

## 数据包

`.ycp` 外层：8 字节 `YICHAT2\0`，16 字节随机 salt，12 字节随机 IV，其后为 AES-256-GCM 密文及标签。PBKDF2-HMAC-SHA256 10000 次派生密钥，AAD 为魔数。明文为 gzip 压缩 UTF-8 JSON。口令留空时使用公开默认混淆口令 `YiChat-data-package-v2`；需要保密时自行设口令。包不包含登录凭据、Keystore 密钥或 Matrix 私钥。压缩包最大128MiB，解压内容最大256MiB，最多10000个会话日期项。

内层 `format=yichat-data-package,version=2,created_ms,days,files`。每个日期含 `room_id,day,complete,entries` 和可选预计算摘要；`files` 以文件 ID 去重，保留实际 base64 数据。空日期必须显式存在。完整包与不完整包合并时完整包覆盖；其他会话并入。同日内容有分歧则由用户选择来源，不能信任包内声明的摘要来跳过内容比较。

App 可选择本机全部会话、一个或多个会话，以及全部本机不可撤回数据或指定 GMT 日期范围，在本地导出；定期外部导出同样为 YCP，包含已保存的原件和附件。旧 YCB 恢复接口保留兼容。网页 `/data-packages` 独立页面只在用户点击校验后上传所选数据，合并和预览完全本地运行。一次校验一个会话连续1～7个 GMT 整日，缺失空日期也不能省略。

导入同日不一致时分别校验本地和导入数据。通过的版本覆盖整个日期；若双方明确不匹配，先预览两方，再次确认保留哪一方，并保持整日灰色“不可信”。网络错误或服务器尚未封存不能当作校验失败，保持待处理。

在线校验失败时整日灰色。七天内服务器分发完整日期，客户端安装后重新计算并确认；已删除原件则向仍在会话的在线 App 请求完整日期。服务器对对端提供的真实消息及已下载文件重新校验整日摘要，未下载文件保留原摘要，通过后才中转；接收方覆盖整日后再次校验。无人持有通过校验的完整数据时维持灰色，不能伪造恢复成功。

## 会话端接口

所有 `/api/` 接口要求 YiChat 登录与相应历史权限。

- `POST /api/messages/{room}/share`：`{"url":"白名单地址","txn_id":"唯一请求键"}`，或 `content` 代替 `url`；返回服务器发布的 `event_id`。重试复用请求键。
- `POST /api/materials/check`：`messages` 数组，每项 `event_id,payload_sha256,sha256`；返回 `ok/replace/expired`、原件元数据和必要时的下载路径。
- `GET /api/materials/{id}?event={event_id}`：原始规范 JSON 字节。
- `POST /api/materials/{event_id}/confirm`：设备重新计算的 `payload_sha256,sha256`，返回 `ok/mismatch`。
- `GET /api/packets/days?room=&start=&end=`：已封存摘要及条数，含空日。
- `POST /api/packets/check`：`room,start,end,days`，每项 `day,complete,sha256,count`；只交换完整性元数据。
- `POST /api/packets/verify`：`room,start,end,days`，发送所选实际完整数据（已下载附件带实际字节，未下载附件只带保存的 id/bytes/sha256）；支持 `Content-Encoding:gzip`。返回整日状态和消息预览；某日消息不匹配时该天全部不可信。
- `GET /api/packets/day?room=&day=`：保留期内原始整日数据；已删除时返回有期限的 App 互助请求。
- `POST /api/packets/exchange`：仅已登记的当前 App 设备，最多7个请求/提供，完整日期校验成功后中转；180秒有效，内存暂存总额最多256MiB。

部署必须先备份，然后使用 `python -m backend.manage material-migrate` 授权一次性旧特殊 URL 消息清理。迁移未完成前不生成新整日摘要，避免将待删除旧材料混入固定摘要。生产切换和测试记录见对应版本部署文档。

## 测试

先安装 Python 测试依赖、前端 npm 依赖并构建 `client/dist`，准备专用 `yc_test_*` PostgreSQL 账号和数据库；测试拒绝对生产数据库执行清表。然后执行 `pytest source/yichat/tests`。浏览器测试需要 Playwright 和 Chromium，`PLAYWRIGHT_MODULE`、`PLAYWRIGHT_CHROMIUM_EXECUTABLE` 可指定现有安装；未设置时按 Node/Playwright 默认规则解析。新版数据包浏览器测试会启动独立无代理的本地 Vite 和 HTTP 服务，无需连接生产站点。

前端执行 `npm --prefix source/yichat/client test`。Android 执行 `:app:testDebugUnitTest`；`ProtocolStorageTest` 使用真实 Room、Keystore、原件文件和附件分块验证断网存储及导出，需要可运行的 Android 设备。`packet-protocol.test.ts` 与 `PacketProtocolTest.kt` 同时覆盖 Python 摘要向量、浏览器到 Kotlin 和 Kotlin 到浏览器的压缩加密包互通。

## 0.4.1：发布记录访问凭证与按需附件

分享 URL 是对所选记录的访问凭证，不授予访问源会话其他消息的权限。发布时持久保存原记录哈希、源会话、消息 ID、身份显示资料与附件描述，不延长服务器原始正文和附件的七天实体保留。原生设备已备份的记录可直接读取；未备份则先请求服务器。服务器优先读取有效原件，再尝试以源消息和固定身份资料重建并比对原发布哈希。源消息不足时返回202 pending，由仍在源会话的在线 Android 设备提供所选原始数据；服务器按原记录哈希核验后限时中转。仅所选附件可补齐，并按发布时保存的附件摘要核验。无在线有效备份时保留未获取状态，不宣称恢复成功。未发布、未知或已清空源会话的凭证不能用来申请他人数据。

`POST /api/records/exchange` 仅已登录原生设备可调用：空请求返回当前源会话的 `asked`；`offers:[{hash,canonical,files:[{id,data}]}]` 提供数据。服务端限制队列、内容尺寸、暂存时间和总中转字节；哈希、源会话身份和所选附件范围均须匹配。匿名持有链接者通过原 `/api/public-records/{hash}` 轮询，不获得整日数据或其他会话权限。

新版聊天记录材料的消息信封增加 `org.yitiao.record_access`，原件丢失时仍可通过访问凭证补齐所选记录。该字段不是客户端直接发送原件的授权。消息列表滚动到顶部自动加载，原始消息七天范围以外的本机记录下方显示“以上是本机消息”。转账和收款气泡由服务器发布及重试，前端不提供补发转账气泡按钮。
