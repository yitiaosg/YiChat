# 网关 + YIDT 升级：桌面端钱包回跳（规格，待实施）

状态：**已于 2026-11-06 部署到生产**（见文末部署记录）。依赖 [DESKTOP-LOOPBACK-LOGIN.md](DESKTOP-LOOPBACK-LOGIN.md)（已部署）。Android 行为不变。桌面客户端的对应部分由 `desktop/` 另行实现，见文末。

## 现状与问题

原生设备在两处得到的是 Android 专用的回跳地址：

1. **钱包绑定**：`GET /api/wallet` 对所有原生设备（`native_devices` 有记录）返回 `bind_url=https://yidt.yitiao.org/services/yichat/?client=android`。YIDT `services()` 在 `client=android` 时，绑定完成后跳到 `https://chat.yitiao.org/app/return`，由 Android App Link 接住。
2. **付款订单**：`Funds.create(..., native_return=True)` 把 `return_url` 写成 `https://chat.yitiao.org/app/return?order=<oid>`。YIDT `authorize()` 遇到这个前缀时，**不附加** `yidt_intent`，原样跳转；App 收到后加载 `/?order=<oid>`，查询原订单，并要求用户在 App 内再次明确确认出资。

桌面浏览器接不住 App Link：用户停在静态的 `/app/return` 页面（“请打开已安装的 YiChat”），桌面 App 收不到订单号。

## 方案

桌面端已在 HKCU / `.desktop` 注册 OS 协议 `yichat-open:`（第 2 阶段用于 Toast 激活）。回跳仍落在 **chat.yitiao.org 的 https 页面**（保持 YIDT 只回跳到固定可信源的约束），再由这个静态页面转到 `yichat-open://return…`。这和现有的 `/app/callback` + `/app/native-return.js` 是同一种做法。

协议回跳只携带固定格式的订单号，不携带 `yidt_intent`、令牌或任何秘密。即使本机有其他程序抢注了这个协议，也只能拿到订单号；出资仍需桌面 App 的会话，并由用户再次明确确认。

## 改动清单及目的

| # | 位置 | 改动 | 目的 |
| --- | --- | --- | --- |
| 1 | 网关 `native_auth.migrate()` | `native_devices` 增加可空列 `platform`（NULL 表示 android） | 区分设备平台 |
| 2 | 网关 `/auth/native/exchange` | 新设备插入 `native_devices` 时，写入 `native_codes.platform`（为空就写 `android`）。恢复设备时保持原值；原值为空且这次登录平台是桌面时，可以补写 | 让后续接口知道设备平台 |
| 3 | 网关：新增查询函数，例如 `native_platform(c, subject, device)` | 返回 `None`（非原生）/`android`/`windows`/`linux`，并替换 `app.py` 中三处 `native_devices` 存在性查询里与回跳有关的两处（`/api/wallet`、`/api/orders`） | 平台分流 |
| 4 | 网关 `GET /api/wallet` | `bind_url`：android → `?client=android`（不变）；windows/linux → `?client=desktop`；网页 → 无参数（不变） | 桌面绑定后能回到 App |
| 5 | 网关 `Funds.create` | 参数从 `native_return: bool` 改为 `native_return: None|'android'|'desktop'`。android → `/app/return?order=<oid>`（不变）；desktop → `/app/desktop-return?order=<oid>`；网页 → `/orders/<oid>`（不变） | 桌面付款后能回到 App |
| 6 | 网关：新增 `GET /app/desktop-return` 和 `GET /app/desktop-return.js` | 静态 HTML 加外部脚本，**不在服务端反射任何查询参数**，设置 `Referrer-Policy: no-referrer`，不引用第三方资源。脚本只接受两种查询：无参数（绑定完成），或者只有一个 `order` 且匹配 `^[A-Za-z0-9_-]{32}$`；同时要求 `location.origin === 'https://chat.yitiao.org'`。合法时生成 `yichat-open://return` 或 `yichat-open://return?order=<oid>`，设为按钮链接并尝试 `location.replace`；不合法时提示“回跳无效，请返回 YiChat”。页面文字说明：没有安装桌面版或浏览器拦截时，可以手动打开 YiChat 桌面版，App 会在聊天中显示最近的订单 | 把 https 回跳转交给桌面协议 |
| 7 | YIDT `yidt.py` `services()` | 接受 `client` ∈ {`android`, `desktop`}，其他值仍按网页处理。`continuation` 保留同一个 `client`（经过 PIN 设置流程后不丢失）。绑定完成后：android → `.../app/return`（不变），desktop → `.../app/desktop-return` | 绑定完成后回到桌面 |
| 8 | YIDT `yidt.py` `authorize()` | 不附加 `yidt_intent` 的固定前缀，从只认 `https://chat.yitiao.org/app/return?order=` 扩展为同时认 `https://chat.yitiao.org/app/desktop-return?order=`。批准和取消都原样跳回 | 桌面付款不把 intent 带进 URL，仍由 App 再次确认出资 |
| 9 | 测试 | 扩展 `tests/test_native_wallet_return.py` 和 `tests/test_native_auth.py`（见下） | 回归 |

**不改**：资金结算、幂等、手续费、`yidt_intents` 结构、Caddy、Synapse、Android App Link 与 `/app/return` 页面。

## 测试要求

- `/services/yichat/?client=desktop` 绑定后，302 到 `https://chat.yitiao.org/app/desktop-return`；`?client=android` 和无参数的结果不变；`?client=https://evil.example/` 和其他任意值都按网页处理。
- PIN 设置流程中，`client=desktop` 的 continuation 保持不变，CSRF 校验不能被绕过（参考现有的 android 用例）。
- 桌面设备（windows 和 linux 各测一次）的 `/api/wallet` 返回 `?client=desktop`；android 设备和旧设备（`platform` 为 NULL）返回 `?client=android`；网页会话不带参数。
- 桌面设备创建订单，`return_url` 应为 `/app/desktop-return?order=<oid>`；`authorize` 的批准和取消都原样跳转，不带 `yidt_intent`。网页订单仍附加 `yidt_intent`。
- `/app/desktop-return?order=<script>` 等非法值：页面不反射，脚本拒绝。页面响应包含 `no-referrer`。
- 迁移可以重复执行；已有的 `native_devices` 行 `platform` 为 NULL，行为与 android 相同。
- 回跳相关的现有测试全部保持通过（其中一部分在缺少 Synapse 的隔离环境中原本就是 ERROR，前后对比结果需一致）。

## 部署注意

- 涉及两个服务：网关 `yichat.service`（`/root/yitiao/yichat`）和 YIDT（`yidt.service`，gunicorn `yidt:app`，`/root/yitiao/yidt.py`）。两者都要重启，**先部署网关，再部署 YIDT**。如果先部署 YIDT，它只是多认一个暂时还用不到的前缀，无害。
- 进行中的订单：已创建的 android 或网页订单的 `return_url` 不受影响。
- 回退：两边代码回退并重启即可；`native_devices.platform` 列可以保留。

## 桌面客户端配套（由 desktop/ 实现，不在服务器）

- 解析 `yichat-open://return` 和 `yichat-open://return?order=<32 位>`（通过单实例转发或启动参数），把 WebView 导航到 `/` 或 `/?order=<oid>`。共享前端已有 `?order=` 的处理：查询订单、打开会话、显示“返回会话”弹窗、再次确认出资。
- Linux 的 `.deb` 和 AppImage 注册 `x-scheme-handler/yichat-open`；Windows 由程序启动时和 NSIS 安装包写入 HKCU。

## 部署记录（2026-11-06）

- 网关：`93e066e` → `eb0fec6`；YIDT：`93e066e` → `5eb9f66`（在 `eb0fec6` 之上）。部署分支是 `deploy/yichat-server-v2`。每个提交只含本次改动。
- 迁移：`native_devices` 新增 `platform` 列。现有 6 行都是 NULL，按 android 处理，回跳行为不变。
- 测试：只用本机测试库。相关文件补丁前 113 个用例（112 通过，1 失败），补丁后 144 个（143 通过，1 失败）。唯一的失败 `test_metadata_binds_room_device_txn_and_original_ciphertext` 部署前就存在，原因是停用端到端加密后返回 403。回跳脚本用 node 实际执行验证了 16 种情况。
- 只读 curl：`/app/desktop-return` 和 `.js` 都返回 200，响应带 `no-referrer`，页面不反射参数；`/app/return` 未改动。公网响应末尾有 Cloudflare 插入的检测脚本，网关本机端口的响应就是代码里的固定页面。
- 实施时与规格有出入的地方：`/app/desktop-return` 放在 `app.py`；`Funds.create` 只接受 `None/'android'/'desktop'`，其他值会抛出 ValueError；`native_auth.migrate()` 遇到还不存在的表会跳过；`test_push.py`、`test_native_transactions.py` 的夹具改成了显式列名。
- **本仓库 `source/` 尚未同步这次的服务端代码**：生产提交 `eb0fec6`、`5eb9f66` 只在服务器的部署分支上。需要在服务器导出补丁，回填到本仓库。
  - **已回填（2026-11-06）**：仓库提交 `0ba5940`。用 `git format-patch` 从部署分支导出 `eb0fec6`、`5eb9f66`，应用到 `source/`，并同步了 `test_push.py` 的显式列名修改（这个文件在服务器上没有纳入 git），`SHA256SUMS` 已更新。`funds.py` 需要减少上下文才能应用，没有内容冲突。回填后 `source/yichat/` 下 7 个文件与生产文件逐字节一致。`source/yidt.py` 与 `5eb9f66` 中的版本一致，但与生产文件不同：生产 `yidt.py` 还有更早的未提交改动（区块浏览器页面、未锁定授权直接批准、favicon、绑定与钱包成员关联查询），这些不属于这两个提交，没有回填。
  - **YIDT 已按生产文件同步（2026-11-06）**：`source/yidt.py`、`source/yidt_core.py`、`source/yidt_bridge.py`、`source/templates/yidt.html` 已整体替换为生产版本，与生产文件逐字节一致；`SHA256SUMS` 已更新。后三个文件一起同步，是因为生产 `yidt.py` 依赖其中的 `service_address()`、`yidt_blocks` 表和 `block` 页面。
