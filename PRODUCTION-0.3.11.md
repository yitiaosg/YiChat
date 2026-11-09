# YiChat 0.3.11 · 会话手势、分享与设置

Android versionCode 19，使用原签名证书覆盖升级。网页与 APK 使用同一前端源码。

- 钱包改用 Lucide Wallet，应用主题色和单个会话背景/主题色改用 Palette，右上角新增 ListChecks 多选入口，与现有图标保持一致。来源：[Wallet](https://lucide.dev/icons/wallet)、[Palette](https://lucide.dev/icons/palette)、[ListChecks](https://lucide.dev/icons/list-checks)，使用项目已有的 lucide-preact / ISC 许可证。
- 消息免打扰、禁止分享图片与合并聊天记录改为独立滑动开关，统一排列在会话详情下方。保留原权限、禁用和保存状态。
- 消息只允许从右向左滑动引用；向右滑和垂直滚动不触发引用。
- 长按普通消息会全选正文，并在满足服务器保留、超过5分钟等条件时选入多选；特殊预览只选择消息，不复制文字，也不误打开卡片。保留可点击的复制文字操作。
- 多选模式底部悬浮显示分享图片、逐条转发、合并转发，按会话分享权限及49条图片上限禁用；可点消息或复选框切换选择。进入多选时保留输入草稿。
- 合并记录每条消息显示头像和昵称。新记录在服务器快照中加入昵称和头像地址；旧记录打开时补查发送者资料，原记录内容及哈希保持不变。长图继续按连续发送者合并身份，可关闭头像/名字；生成前等待头像和卡片加载。

验证：前端37项测试及 TypeScript/Vite 构建通过；分享/资料接口22项后端测试通过。更新一项旧测试，使其符合当前已停用消息端到端加密的服务器行为。Chromium 手机触屏模拟检查了单向引用、全文长按、多选卡片、草稿保留、悬浮按钮、服务器删除/近期/嵌套记录限制、合并发送和头像昵称、底部开关及禁止分享；另实际导出5条消息 PNG，检查转账、收款、红包、站内交易和斗地主预览。

Android 严格依赖校验的签名 Release 构建与18项 JVM 测试通过，签名证书与上一版一致，zipalign 16KiB检查通过，APK内14个静态文件逐一与发布构建比对一致。未做物理手机手势验收。此次不修改资金账本、收费规则或文件存储协议。

浏览器回归夹具：`source/yichat/client/test/browser/sharing.html`。启动本地 Vite 后，运行 `sharing-check.mjs`；可用环境变量 `PLAYWRIGHT_MODULE`、`PLAYWRIGHT_CHROMIUM_EXECUTABLE`、`YICHAT_BROWSER_BASE` 和 `YICHAT_BROWSER_OUTPUT` 指定本机依赖、浏览器及输出路径。夹具仅使用模拟账号与接口，不连接生产账户。

APK SHA-256：`8255d7e12bccf6f95f56abe5cce3c4497a80e30520f13f6bd5cb94a1f1738be3`。
