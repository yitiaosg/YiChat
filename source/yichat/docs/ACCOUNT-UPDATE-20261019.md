# 账户页面与YIDT后续更新 · 2026-10-19

本轮承接首轮迁移，实施分支仍为 `refactor/yichat-server-v2`，生产分支 `deploy/yichat-server-v2` fast-forward部署。无新数据库结构迁移，不改变用户uid、MXID、原密钥、金融账本或已封存归档。

## 页面

- 我的二维码不再使用白色底图或外层卡片，改为透明底白色码点，内容区及二维码在桌面/390px手机均居中；保留扫码说明、当前主页URL和返回入口。
- 个人资料、安全页面不再渲染账户首页头像摘要、关注数量、签到、钱包或其他一级菜单。隐私、二维码及名单页面也仅包含本页内容和返回导航。
- 隐私页使用自定义轨道开关、暗色下拉框、统一圆角输入框、保存按钮和黑名单操作样式；键盘可操作开关，焦点可见。两步验证开关增加无障碍名称和开关状态。
- 原账户主页的签到、钱包和既有业务保留。

## 时间和资金

私聊、群聊均统一30日过期，GMT+0/UTC自然日结束+30×86400秒后，在存证门控满足时清理；没有恢复7日私聊规则。`/api/session`新增retention对象，公开days=30、timezone=GMT+0、basis=utc_day_end及清理需封存条件。资金入金后的24小时收款/领取期限独立保留。

用户已明确允许使用YIDT功能。本轮对原账本执行只读校验，核实既有官方托管钱包存在、无已入金YiChat订单和待出金任务，然后设置生产：

```ini
YICHAT_OFFICIAL_WALLET=yidt-000000000000
YICHAT_FUNDS_ENABLED=true
```

未创建新钱包或同名余额，没有读取/输出原钱包私钥，没有真实资产试转。用户仍必须验证绑定自己的钱包，在真实YIDT授权页明确批准金额、钱包和费用，再执行原有入金/收款/领取流程。依赖和对账失败仍拒绝操作。配置示例继续默认false以保护未核实的新安装；生产当前开关由health/session提供。

## 测试

| 检查 | 真实结果 |
|---|---|
| YiChat完整后端，包括真实Synapse/PG/归档浏览器 | 82 passed，1条现有TestClient/httpx弃用警告 |
| 主站账户/OIDC/退出/二级页隔离/二维码像素 | 6 tests OK |
| 原YIDT、PIN、Passkey | 45+8+7+10 tests OK |
| 账户Chromium交互和布局 | 16 passed，0脚本错误 |
| 透明二维码 | PNG只有透明像素及不透明纯白码点，无不透明黑色/白色底板 |
| 桌面/手机 | 图片中心与视口中心误差<1px，390px无横向溢出 |
| 隐私 | 真正提交保存、拉黑/解除、滚动进入可视区后懒加载，键盘切换开关 |

浏览器fixture每轮使用新临时库；未拿生产用户会话代替测试账号。截图位于隔离工作树 `yichat/tests/browser/artifacts/qr-desktop.png`、`qr-mobile.png`、`privacy-mobile.png`。首次测试暴露二维码库API调用不匹配，已修正并通过实际PNG和浏览器校验；黑名单测试改为等待页面重载后滚动到懒加载区域，最终完整流程通过。

本轮改动前备份：`/root/yitiao/backups/yichat-v2-20261019T161943Z/`，含原env/密钥私密包、SQLite在线快照和PG dump，可用于对照。回退页面可退回前一提交；若需停用资金只将生产开关设为false并重启网关，不覆盖新账本或删除财务队列。

## 上线核验

运行代码提交 `b920a3e` 已fast-forward到生产并重启website/yichat。线上HTTPS `/health` 返回200、status=ok、funds_enabled=true；管理status显示official_configured=true、采集healthy、水位18、6份封存且0缺口，reconcile无问题。页面新CSS返回200，匿名二级页按原认证方式返回302或401，YIDT服务绑定入口正常。website、yichat、synapse、archive、yidt均active；本轮检查website/yichat ERROR journal为空。

以更新前备份逐项比较，yidt_transactions末端哈希未变，没有资金试转。更新后配置/密钥及数据库备份已写入 `/root/yitiao/backups/yichat-v2-20261019T162915Z/`。这次只更改两项已核实的YIDT配置，不轮换密钥，也未改动原账本余额。

完整内核接口文档：[CHAT-KERNEL-API.md](CHAT-KERNEL-API.md)。首轮DEPLOYMENT-RECORD和verification.json记录的是本轮启用前历史，不代表当前资金开关。
