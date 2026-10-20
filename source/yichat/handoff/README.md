# YiChat Android 新会话交接包

先阅读本文件，再把 [ANDROID-PROMPT.md](ANDROID-PROMPT.md) 全文发给新的 Codex Cloud 会话。该包是现有服务和 Web 客户端的源码交接材料，不是已完成的 Android 工程或 APK。包内没有生产数据库、登录凭据、设备私钥、归档正文、上传文件、签名 keystore 或服务器运行环境。

## 解压和完整性

```sh
tar -xzf yichat-android-handoff-20261020.tar.gz
cd yichat-android-handoff-20261020
sha256sum -c SHA256SUMS
cat SOURCE-MANIFEST.json
```

根目录 `SOURCE-MANIFEST.json` 记录源码提交、每个文件摘要、来源及脱敏规则。根目录 `SHA256SUMS` 覆盖包内其余文件；压缩包旁 `.sha256` 校验运输完整性，不构成独立可信的发布签名。源码导出时移除主站/邮件的内嵌凭据默认值，仅保留环境变量读取，生产文件未被改写；两项处理都在manifest列明。`source/` 还原项目相对目录，`handoff/` 是本目录的方便副本。原附件放在 `reference/`，它较旧，接口以 `source/yichat/docs/CHAT-KERNEL-API.md` 和当前实现为准。

## 文件地图

| 位置（相对 source） | 作用 |
|---|---|
| `yichat/docs/CHAT-KERNEL-API.md` | 当前权威接口，含取消订单、原生 App 包交换边界 |
| `yichat/docs/ARCHIVE-V1.md`、`FUNDS.md`、`SECURITY-AND-OPERATIONS.md` | UTC存证、资金不变量、权限和运维 |
| `yichat/backend/` | 真实网关、身份/限流/Matrix代理/订单/赠卡/摘要/归档/Synapse模块 |
| `yichat/client/src/` | 已联调 Web 行为参考：加密、出站箱、媒体适配、分享渲染、资金气泡 |
| `yichat/deploy/` | 示例配置、专用测试PG角色初始化、部署/备份脚本；没有实际 env 或 DSN |
| `yichat/tests/`、`yichat/client/test/` | 隔离账本/Synapse/PG/浏览器故障与幂等测试、协议样例 |
| `oidc_provider.py`、`website.py`、`account_social.py`、`passkeys.py` | 主站 OAuth、不可变UID、资料、退出与隐私策略 |
| `yidt*.py`、`templates/yidt.html`、`static/yidt*` | 原钱包绑定/批准/账本/返回流程 |
| `ycards*.py`、`static/ycards*`、`docs/YCARDS*` | 真实卡片读取、模板、损耗、原渲染器及赠送相关业务 |
| `parachute.py`、`static/parachute*`、`templates/parachute*` | 原游戏与只读分享渲染逻辑；不得将游戏写入行为放进预览 |
| `tests/`、`static/`、`templates/` | 主站依赖、测试夹具、相关字体图像与页面源码（非用户内容） |
| `yichat/tools/verify_archive.py`、`docs/samples/` | 本地归档验证与人工合成样例，无真实聊天正文 |

旧 `yichat/web/order.html`/`order.js` 仍作为历史参考随源码保留，当前服务器订单URL返回聊天界面；不要重新启用独立订单详情页。旧文档按发布日期理解，最新增量记录优先。没有旧 Telegram 客户端、交易机器人策略、生产备份或 Git 历史。

## 在新机器上运行参考测试

需要 Linux、Python3.11+、Node22.12+、npm10+、独立本地 PostgreSQL、Chromium 所需系统库。可用普通开发用户运行：

```sh
cd source
bash yichat/handoff/bootstrap-tests.sh
# Debian 新建隔离机器如缺 PostgreSQL：sudo apt-get install postgresql；启动本机服务。
.venv-handoff/bin/python yichat/deploy/provision_archive.py --test
PYTHONPATH=. .venv-handoff/bin/python -m pytest -q yichat/tests -s
```

`provision_archive --test` 用 sudo -u postgres 新建固定名称 `yichat_archive_test` 和 `yc_test_*` 最小权限角色，将随机测试密码存到运行用户的 `~/.cache/yichat-test-dsns.json`（0600），不会输出密码。存在同名角色但没有对应私有清单时须先确认它们属于可丢弃测试实例；不要删生产库解冲突。测试会清空测试schema；禁止复用生产DSN，禁止同时运行多套依赖PG的测试。浏览器夹具内登录和控制路由仅在 loopback 临时服务注册；不能部署到生产。

目前内核Python锁包含网关/Synapse测试依赖；`requirements-main.in` 补齐主站夹具需要的包和本机已验证版本。Android依赖需在新会话核实、锁定并实际构建，不包含预下载SDK/JDK/Gradle/第三方AAR。网络策略、代理/CA、JDK与Android SDK/NDK等在新Cloud环境重新检查，不复制旧会话缓存或认证信息。

## 生产连接和已有边界

生产 `chat.yitiao.org`、`yitiao.org`、`yidt.yitiao.org` 用于最终人工验收，禁止拿真实资金/资产试跑。新会话无服务器访问权限时先完成本地 App、隔离后端补充及签名APK；单列待部署内容，不宣称原生登录/回跳已在生产工作。Web登录现有cookie OAuth已经可用；App系统浏览器到Bearer会话的安全交接尚未实现。

没有推送网关/FCM配置材料；不能承诺杀进程仍实时收信或自动恢复。App持久消息备份/恢复及原生密钥管理要在下一会话实现，Web只作临时客户端。保留密钥与消息包是两个独立条件；仅恢复Matrix密钥不能凭空恢复服务器已删除且无人持有的密文。

建议新建顶层 `android/` 工程，把 `source/` 当现有系统参考。现有根 `.gitignore` 默认忽略未知扩展；若在source内开发Android，必须补齐.kt/.kts/.xml/.toml/.properties/Gradle wrapper等允许规则，并继续忽略local.properties、签名文件和构建输出。
