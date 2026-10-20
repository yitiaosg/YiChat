请基于我上传的 `yichat-android-handoff-20261020.tar.gz`，直接完成正式 YiChat Android App 的开发、隔离联调、测试和可安装签名 APK 交付。先给简短步骤，然后持续实现，不停在方案、页面壳、假数据演示或只能编译的样板工程。

## 先读材料、确认基线

1. 解压并校验根目录 SHA256SUMS，阅读 SOURCE-MANIFEST.json、handoff/README.md、handoff/ARCHITECTURE.md，以及工作目录适用的 AGENTS.md。
2. 在 source 下先读 yichat/docs/CHAT-KERNEL-API.md（当前接口依据，含第16节取消订单），再读 ARCHIVE-V1.md、SECURITY-AND-OPERATIONS.md、FUNDS.md、WEB-CANCEL-20261020.md 和相关实现。reference 内原附件较旧，不能覆盖新增约定。
3. 复用当前 OAuth、SID联动退出、Matrix网关、业务/账本、隐私、归档和现有渲染器，不重写聊天内核，不恢复 Telegram 源码。包内没有生产数据、密钥、token、实际环境文件和历史记录；不要索取/伪造生产私钥作为开发前提。
4. 新Codex Cloud会话不当然具有 /root/yitiao、原服务器权限或部署凭据。先检查本机网络/代理/CA、JDK、Android SDK/NDK、Gradle、Rust/AAR。完成本地可审查实现和隔离APK联调；确需最小服务端补充时修改包内source、文档、测试、提供迁移/部署/回滚步骤。不能没部署就宣称线上原生登录回跳可用。

## 产品定位和确定技术栈

只做Android，正式聊天客户端，手机为主，平板自适应。Kotlin + Jetpack Compose + Material3 + Navigation + ViewModel/StateFlow/Coroutines；Repository分层和单向数据流。官方 Matrix Rust SDK 完整SDK Kotlin UniFFI AAR 负责原生E2EE/同步/密钥持久化；业务HTTP OkHttp + kotlinx.serialization；Room保存App出站箱、业务幂等记录、认证摘要、原事件包和同步水位，Android Keystore包装账号独立的本地数据密钥；WorkManager负责受系统约束的备份和增量任务。参考包内ARCHITECTURE.md，核实并固定可下载的稳定依赖、版本目录/Gradle Wrapper和依赖校验，禁止动态版本。建议 applicationId `org.yitiao.yichat`、minSdk26，compile/target SDK选实际可构建的受支持稳定版本。

Compose实现会话列表、聊天区、搜索/未读、历史分页、回复/编辑/撤回、详情/成员/权限/邀请/本人退出群、用户搜索新建私聊/群、资料头像/自我介绍、深浅主题和可访问性。加载历史保持滚动位置，阅读中不强制滚底，显示新消息提示。按钮都必须有实际功能；缺能力就隐藏或明确禁用原因，没有假按钮、占位卡片或伪造正文。

## 系统浏览器登录和钱包操作（硬要求）

主站 `https://yitiao.org`，聊天 `https://chat.yitiao.org`，钱包 `https://yidt.yitiao.org`。网页登录、2FA、YIDT绑定/授权以及主站等操作拉起系统浏览器，不能在WebView内登录或支付，不能读取浏览器HttpOnly cookie，不能让用户粘贴token。可用系统浏览器Intent或其Custom Tabs；返回App后查询权威状态。

现有Web OAuth成功只设置cookie，**尚没有原生App获取Bearer的安全交接接口**。务必先完成最小原生登录适配：固定白名单回跳URI、随机state/nonce、PKCE S256、短TTL一次性交换码和TLS POST换gateway session，核实主站签名uid/SID；长期token/私钥不进URL、日志、APK。保留Web OAuth流程与全站退出规则。App注册HTTPS App Links精确回调路径；生成与实际随机APK签名证书匹配的assetlinks.json配置，测试已安装/未安装、浏览器取消、恶意回跳/重放/超时。不能把新的网关交接码接口误称为现成OIDC接口直接套AppAuth；有条件使用AppAuth也要实际兼容。

现有Matrix代理不默认支持Rust SDK命名空间Sliding Sync。先用实际AAR打通restoreSession→同步→房间→双账号加密；确有缺口时只补确切受控路由/超时/SID复核，不放开整个unstable/admin/register/federation或跳过业务建房入口。SDK homeserver必须chat域名、access token用网关token，不直连Synapse，不请求主站密码或钱包支付密码。

## 身份、E2EE和可靠性

- 不可变uid、username、MXID、device_id、SID明确区分。账号/设备库隔离，同一库单写者；进程重启/刷新会话/重新授权不误删或复用其他账号密钥。尽量恢复原设备会话；确实换新设备时明确验证和历史密钥恢复流程。
- 私聊原生E2EE，群聊明文，旧加密群按server encryption兼容；禁止明文降级、自创通信加密、明文私钥上传。设备验证、交叉签名和原生加密密钥备份/恢复必须实际运行；恢复密钥与消息备份不是一回事，恢复失败明确提示。
- 加密发送前确认SDK掌握当前双方joined membership及目标设备，不能因为懒加载漏成员导致返回钱包后新消息只有发送者能解密。参考Web已有prepareEncryption修复的语义，用Rust SDK原生方式完成。
- 退出停止同步、撤销会话、清预览缓存/临时文件并锁定本地数据，不擅删历史、设备密钥和归档。主站SID撤销及时失效。
- 所有SDK和业务请求集中预算（服务端按uid共享10次/秒），共享429冷却、Retry-After、批量资料和预览合并；Rust内部请求不能被漏掉，不能声称只加OkHttp拦截器就完成。稳定txnId持久重试，发出/失败/重试可见，未知结果查原请求，不重复消息/资金操作。
- 普通消息和编辑最多2000 Unicode码点。UTC存储，客户端系统时区显示；归档始终UTC自然日。
- 附件实际上传字节≤100000000，私聊本地按Matrix标准加密。room上传、外层附件ID与密钥内层关联按文档，核实Rust高层API有没有丢自定义外层字段。进度/取消/重试，录音渐进权限，图片/音视频懒加载；410和503分开，不运行上传HTML/SVG。遵守真实上传速率/并发，不并行绕过。

## 转账、红包、赠卡及预览

沿用Web目前的微信式转账/红包气泡，收款后给发送者“已收款”回执，待处理/到账/退款均读服务器。App内完成订单查看和明确操作确认，YIDT授权用系统浏览器；付款返回不打开独立“YIDT订单详情”网页，不自动fund。

转账仅私聊：核对双方和费用→独立业务幂等key创建订单→真实YIDT批准→回App用户明确fund→发资金气泡→指定对方主动receive→权威到账。红包仅群：标题/总额/份数，至少0.01×份数，独立授权fund后发气泡，群成员主动claim；金额随机由服务器执行，显示记录/手气最佳/本人金额/24小时截止和退款。金额用十进制字符串及BigInteger/BigDecimal，绝不Double/Float。根据actions控制按钮，处理并发拒绝和未知结果，所有可见订单合并低频刷新，终态停止。

确认转账/红包页面必须包含“取消订单”，二次明确确认：本次不再入金、不发消息。真实调用 `POST /api/orders/{id}/cancel`；只付款人可取消未入金订单、原授权同时失效；取消响应丢失先查原id，已入金409，不拿取消当退款。原创建key重试仍得到原cancelled订单；用户重新开始才建新key。实现服务器fund/cancel并发互斥，旧授权页不能复活订单。绑定失败显示真实bind_url和服务端原因，未启用资金时说明。

赠卡选择本人可赠真实卡片和对象，预览确认后转移资产，再独立发送关联消息；重复发送不能再次赠送。聊天框直接渲染真实赠卡快照，不只文字或链接。

站内分享识别文档白名单游戏/YCard/YIDT交易/订单。发送前可取消预览，发送后双方可视时自动取数据并客户端渲染，也识别历史纯URL消息。私聊只在本地解密后提取所需URL/room发preview，不上传正文。跳伞直接呈现场景、位置和真实快照，复用原场景布局/SVG，未结束结果不能泄露；不执行游戏写操作。YCard复用真实模板/实例/损耗和现有卡面材质光效渲染，必要时只为**包内可信只读卡面渲染器**使用隔离WebViewAssetLoader，不承载登录/支付/聊天网页、不加载第三方HTML、不暴露token私钥；离屏暂停。YIDT金额真实精度、来源目标/时间/hash，点击系统浏览器看原站。缺资源/无权限明确降级普通链接。

## App才有的自动备份与恢复

Web定位临时使用，不能让浏览器参与在线成员补包；App持久保存已认证原canonical消息包/摘要/水位和撤回tombstone，并提供真正的自动备份、状态、恢复入口。用户选SAF目录的外部备份采用成熟库加密，格式版本化、完整性校验，跨设备不能只复制Keystore包装文件。Matrix密钥备份独立。

增量以最后成功同步时间前5分钟，重登前10分钟；水位仅可靠提交后前进。服务器仍有的30天内内容直接拿服务器；只有服务器确认已经清理且有可信摘要/权限的缺失包才向**在线App成员**请求，使用已有packets/exchange管线。私聊只交换原密文包、本地对原canonical字节（含LF）做SHA256与认证摘要比对；不信对方自行声明的hash，不上传解密正文/私钥，不重新发送历史。无持有者/无历史密钥则如实显示无法恢复。

撤回限制5分钟、转账/红包/礼物特殊消息禁止撤回由服务器执行。30天内重新进入App补齐撤回事件，先应用tombstone再展示旧缓存，不能恢复已撤回正文。原有归档按UTC、当日临时快照/两种封存规格分清；摘要认证获得后本地验证，不上传归档/正文/私钥、不给实时消息伪造每日归档校验状态，不往统一封存文件加个人字段。

## 联调与APK交付

优先原生客户端适配，确缺接口只最小后端补充并补文档与隔离测试。禁止真实用户资金/资产试跑。参考现有隔离SQLite账本、真实Synapse/模块/PG、原OIDC与YIDT loopback夹具；新环境按README初始化专用测试库，不能复用生产DSN或并发清空同一测试schema。

必须在模拟器实际点击：两账号私聊E2EE和群明文、系统浏览器登录/退出/SID联动、进程重启密钥保存与恢复、邀请/历史/编辑撤回、离线补齐/错误哈希/已撤回包拒绝、头像简介/时区、加密附件上传播放/到期、429退避/请求预算、资金独立幂等/响应丢失/fund与cancel竞态/收款回执、授权回跳不自动入金、双方游戏和YCard可视预渲染、外部加密备份跨设备恢复、账号隔离与签名升级。检查日志/网络，不能泄露密钥、重复扣款或请求风暴。真机不可用时准确标为模拟器结果，不编造手机实测。

交付可安装的APK（至少arm64-v8a，另有x86_64可供模拟器）、源码、构建脚本/配置、测试结果和截图、必要后端部署/回滚说明及明确剩余项。使用**随机生成的新专用自签名密钥**签APK，不用生产签名；首次生成一次，后续版本复用保持升级，不能每次构建换签名。可用handoff/prepare_signing.py作辅助。keystore和密码不进Git、源码tar或日志；需要单独私有交付并明确保存方式。提供APK SHA256、apksigner验签结果和签名证书SHA256/assetlinks配置，验证实际安装和覆盖升级保留本地数据。不要只交未签名APK或把“APK打出来”当成功能已经联调。
