# 网关升级：桌面端回环登录（2026-11-06）

状态：**已审核待部署**。桌面版（Windows/Linux，见 [desktop/README.md](../../../desktop/README.md)）上线前必须先部署这一项。Android 行为不变。

- 源码提交：`441c9d6`（分支 `claude/yichat-desktop-planning-sodnv1`）
- 补丁：[delivery/desktop-loopback-20261106/backend.patch](../../../delivery/desktop-loopback-20261106/backend.patch)，路径相对 `/root/yitiao/yichat`
- 补丁 SHA-256：`118accbd0f995cd81d7ec1b2ca39149406148b2572ebcbe5b35eb11e2991f41c`
- 只改 3 个文件：`backend/native_auth.py`、`backend/db.py`、`tests/test_native_auth.py`。不改 Caddy、Synapse、资金、归档和环境变量，也不新增依赖。

## 为什么要改

Android 用 App Link `https://chat.yitiao.org/app/callback` 接收登录回跳，桌面系统无法接收这个地址。桌面端按 RFC 8252 在本机 `127.0.0.1:<随机端口>` 临时监听，由系统浏览器回跳到这个地址。原来的网关只接受固定的 https 回跳，并把设备名写死为 `YiChat Android`。

## 每处改动及其目的

| 位置 | 改动 | 服务于 |
| --- | --- | --- |
| `native_auth.py` `LOOPBACK` / `redirect_kind()` | 除原 App Link 外，只额外接受 `http://127.0.0.1:<1-65535>/app/callback`：路径固定，不接受 `localhost`/IPv6/https/query/fragment/userinfo/前导零端口 | 桌面端的系统浏览器登录（PKCE 回环回调） |
| `/auth/native/login` | 新增可选参数 `platform`，白名单为 `android`/`windows`/`linux`。不传时按 `android` 处理，因此 Android 0.3.x 的五参数请求不变。回环地址只允许 windows/linux 使用，App Link 只允许 android 使用。PKCE `S256` 仍是必需的 | 区分客户端，防止回跳方式混用 |
| `native_logins` / `native_codes` 新增 `redirect_uri`、`platform` 两列（可空） | 记录发起登录时的回跳地址和平台 | 下面两项绑定 |
| `finish_authorization()` | 只跳转到登录时记录的地址（并再次校验）。列为空时使用原 App Link | 交换码只送往发起登录的那个回跳地址 |
| `/auth/native/exchange` | `redirect_uri` 必须与这个交换码记录的地址完全一致（包括端口） | 防止交换码被转用到其他回跳地址 |
| Matrix 登录 `device_name` | 由白名单平台决定：`YiChat Android/Windows/Linux` | 用户在设备列表里能分辨桌面设备 |
| `native_auth.migrate()`，由 `db.migrate()` 调用 | 用 `ALTER TABLE ... ADD COLUMN` 增加可空列，可以重复执行；`INSERT` 改为显式列名 | 平滑升级。升级时还在进行中的旧登录，新列为空，按原 Android 流程完成 |

测试：`tests/test_native_auth.py` 新增 23 个用例，覆盖桌面端完整流程、端口和回跳绑定、Android 默认与显式平台、18 种非法回跳和平台组合、重复参数、强制 S256、迁移可重复执行。在隔离环境中全量后端测试新增 0 个失败；原有 14 个与浏览器或 Synapse 环境有关的失败，改动前后完全相同。

## 部署步骤（生产目录 `/root/yitiao/yichat`）

1. **核对基线**：生产环境里这三个文件应与提交 `e74016d` 一致：
   ```bash
   cd /root/yitiao/yichat
   sha256sum backend/native_auth.py backend/db.py tests/test_native_auth.py
   # 期望（提交 e74016d）：
   # e9ee066f0cbd9ad3ca988133896f9554671a61bf106b83f10f52d25a66d5db7f  backend/native_auth.py
   # 4a49146a77272e479923d7992e2a0b3b5334cd2e67f6324bf10591f755a67a85  backend/db.py
   # 116a8e1197bbd6f2a0606cc3d76c659faf766de5a4eb4111b95fc82e37400edd  tests/test_native_auth.py
   # 升级后应等于仓库根 SHA256SUMS 中 source/yichat/ 下对应三行
   ```
   如果不一致，**停止**，先报告差异，不要强行打补丁。
2. **备份**：`sudo .venv/bin/python deploy/backup.py`，并记录当前 git 提交，作为回退点。
3. **应用补丁**：`git apply --check backend.patch && git apply backend.patch`，然后单独提交到部署分支。
4. **测试**：在专用测试库运行（不要用生产库或生产凭据）：
   ```bash
   cd /root/yitiao && PYTHONPATH=. yichat/.venv/bin/python -m pytest -q yichat/tests/test_native_auth.py
   # 需要 handoff/bootstrap-tests.sh 所述的本机专用 yc_test_* PostgreSQL；没有就跳过并在报告中说明
   ```
5. **重启**：`sudo systemctl restart yichat.service`。迁移在启动时自动执行。然后检查 `journalctl -u yichat --since '5 minutes ago'`，确认没有 ERROR，并确认 `sqlite3 <gateway db> 'PRAGMA table_info(native_codes)'` 里有 `redirect_uri` 和 `platform` 两列。
6. **只读验证**（不登录任何真实账户）。`S`、`N`、`C` 用 43 个以上的 `[A-Za-z0-9_-]` 随机串，`C` 必须正好 43 个字符：
   ```bash
   B=https://chat.yitiao.org/auth/native/login
   Q="state=$S&nonce=$N&code_challenge=$C&code_challenge_method=S256"
   curl -so /dev/null -w '%{http_code}\n' "$B?$Q&redirect_uri=https://chat.yitiao.org/app/callback"                  # 307 Android 原样
   curl -so /dev/null -w '%{http_code}\n' "$B?$Q&redirect_uri=http://127.0.0.1:53682/app/callback&platform=windows"  # 307 桌面
   curl -so /dev/null -w '%{http_code}\n' "$B?$Q&redirect_uri=http://127.0.0.1:53682/app/callback"                   # 400 回环必须声明桌面平台
   curl -so /dev/null -w '%{http_code}\n' "$B?$Q&redirect_uri=http://localhost:53682/app/callback&platform=linux"    # 400
   curl -so /dev/null -w '%{http_code}\n' "$B?$Q&redirect_uri=https://evil.example/cb&platform=linux"                # 400
   ```
   每次 307 都会写入一条 10 分钟后过期的待处理登录记录，无害。
7. **Android 回归**：用 Android 0.3.7 完成一次登录。客户端不传 platform，走 App Link，设备名仍是 `YiChat Android`。

## 回退

代码回退到原提交后重启 `yichat.service`。新增的两列可以保留，旧代码按列名读取，不受影响。没有删除或改写任何已有数据。

## 未包含（待决定）

钱包绑定和付款回跳目前对所有原生设备都返回 Android 地址（`backend/app.py` 中的 `?client=android`）。桌面端完成 YIDT 授权后会停在浏览器里的 `/app/return` 页面，回不到 App。要支持桌面回跳，需要另行修改服务端，本次不包含。
