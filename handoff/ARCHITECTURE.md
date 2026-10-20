# Android 架构、边界与适配重点

这份文档是下一会话的实现约束，不表示这些原生能力已经上线。当前服务端/Web源码是可运行参考，Android目录尚未创建。

## 确定的技术路线

- 原生 Kotlin + Jetpack Compose + Material3；ViewModel、StateFlow、协程，单向数据流，按功能组织，Repository封装SDK/HTTP/本地数据库。Compose LazyColumn稳定key和分页，保持历史滚动位置；支持深浅主题、系统时区、无障碍及软键盘。
- Matrix Rust SDK **完整SDK的官方Kotlin/UniFFI AAR**，由它负责设备、Olm/Megolm、交叉签名、密钥备份、同步和持久化加密库。不要把 `matrix-js-sdk.initRustCrypto()` 搬进Android，也不要自行实现加密。官方Kotlin发布源：<https://github.com/matrix-org/matrix-rust-components-kotlin>，SDK源：<https://github.com/matrix-org/matrix-rust-sdk>。下一会话选可实际下载、通过网关兼容验证的稳定版本，固定版本与校验，不用动态版本。
- OkHttp + kotlinx.serialization 处理业务API；共享请求预算协调**SDK内部HTTP**和业务HTTP（不是只给OkHttp加拦截器就称完成）。先验证该版本Rust FFI有无HTTP transport hooks。没有时做最小Rust绑定/传输适配，或明确协调SDK重试/同步与全局冷却；用真实抓包证明所有请求共用预算，保留服务端uid限流作为最终约束。目标约6次/秒留余量，401锁会话回登录，429遵守Retry-After/retry_after_ms并共享退避，不换token/连接绕限流。
- Room保存App业务出站箱、独立幂等记录、可信摘要、原canonical包/撤回tombstone/同步水位及必要展示索引；**不另造第二套Matrix Crypto库**。敏感字段/包采用Android Keystore包装的每账号本地随机AES密钥加密；SQLite文件本身若未全库加密，明确记录仍暴露的索引，不声称“Room自带加密”。token和SDK本地库口令同样安全封装，严禁日志/URL。
- SDK设备库路径至少按服务域名+uid+MXID+device_id隔离、单进程单实例；网络重登不等于删除旧设备。WorkManager调度具备网络/电量约束的App备份和补齐，前台同步由生命周期驱动；不承诺进程被杀后永远在线。没有真实推送后端时不展示假推送设置。
- 用户名、昵称、主站头像/自我介绍通过批量资料API；长期关系/资产用不可变uid，Matrix使用MXID/device_id。时间存UTC，普通显示系统时区；归档日期坚持UTC。

Android官方架构参考：<https://developer.android.com/topic/architecture/recommendations>、<https://developer.android.com/topic/architecture/data-layer/offline-first>。这里只采用分层、Repository/Flow与离线读取思路，具体YiChat权限仍由现有服务端决定。

## 必须先打通的两个适配点

### 1. 原生登录与系统浏览器回跳（尚未实现）

当前 `/auth/login → 主站 → /auth/callback` 是Web cookie OAuth。Bearer请求已经被网关支持，但App**不能**从系统浏览器读取HttpOnly cookie，也不能用Web会话token query/剪贴板偷渡。这是下个会话必须补的最小后端能力，包里没有可直接使用的 `/auth/native/*` 接口。

建议网关继续作为现有OIDC受信回调方：App生成随机state、PKCE S256 verifier/challenge，发起短时原生登录事务；网关固定白名单App回跳URI，通过系统浏览器完成现有主站PKCE/state/nonce及SID校验后，只回传短TTL、一次性、绑定原生challenge的**交换码**。App通过TLS POST带verifier换取gateway Bearer及真实uid/MXID/device_id，绑定当前主站SID。交换码重放/超时/错误state或verifier全部拒绝；日志隐藏码，链接绝不带长期token、密钥或正文。原生事务独立于Web cookie登录，不能改变已登录浏览器的设备归属。

若选择直接新增OIDC public client，必须完整实现等价服务端SID校验、网关session/device建立和固定redirect allowlist；禁止在APK放client_secret。不要因选库方便绕过gateway去登录Synapse。

AppAuth-Android可用于符合其接口的OIDC授权；本项目的“网关交接码”不是现成标准OIDC client时，不能假称直接套AppAuth即可，仍需实现状态/PKCE交换。所有用户可见登录、2FA、钱包绑定、YIDT批准、主站/YCard/游戏原站操作都通过系统浏览器（Intent ACTION_VIEW；需要时使用系统提供的浏览器Custom Tabs，绝不嵌入登录WebView）。参考：<https://github.com/openid/AppAuth-Android>。

生产回App使用精确HTTPS App Links路径，例如待实现的 `/app/callback` 和 `/app/return`；不要把整个chat域名所有路径都拦截，避免授权重定向循环。`/.well-known/assetlinks.json` 必须匹配固定applicationId和随机签名证书SHA256。URI白名单、无App时Web回退、用户取消和浏览器被关闭都需测试。没有域名部署权限时完成配置文件与本地验证，明确列出未部署项，不能用“已自动返回App”掩盖。参考：<https://developer.android.com/training/app-links/about>。

钱包授权返回只带可验证业务引用，App重新查询原订单；**返回不自动fund**，让用户明确确认入金或取消订单。绑定通过真实bind_url和原站表单完成，回来再查询`/api/wallet`。原SID退出联动必须继续有效；App退出停止同步、撤销会话、锁本地库，不擅自删除历史、下载档案或原设备密钥。

### 2. Rust SDK同步/媒体与受控网关

当前受控Matrix路由支持原生classic `/client/v3/sync`，没有默认开放Rust SDK常用的命名空间Sliding Sync路由；不要只验能编译就算接通。先用选定FFI版本验证restoreSession、首次同步、房间列表、设备密钥与双账号E2EE。若SDK需要Sliding Sync，只最小添加确切路由/请求体、超时、SID前后复核和权限，不放开整个unstable命名空间、联邦/admin/注册或原生createRoom。`/api/rooms`继续唯一业务创建入口。

媒体：优先按业务 `/api/files/{room}` 上传原始字节（私聊先按Matrix附件协议加密）；原生upload适配需要 `room_id`或`X-YiChat-Room`，原生media download已有受控兼容路由。发送外层必须关联`org.yitiao.attachments:[id]`（或网关`X-YiChat-Attachments`），正文`file`内才放解密参数。Rust高层发送API若丢弃自定义外层字段，必须核实raw/custom event或transport hook适配；不能把密钥放外层，也不能直接发明m.room.encrypted密文。

SDK常会因懒加载拿不全成员：Web已有真实故障修复是在首次加密发送前读取**当前**完整membership state并交给SDK，确认私聊双方设备为加密目标。Rust按其原生接口完成相同语义；不把Web的JS对象操作照搬、不通过盲目重建密钥/会话绕过。

## 消息、资金、分享

- 私聊原生E2EE；新群明文；旧加密群遵从服务器encryption。稳定txnId持久重试，发送/编辑2000 Unicode码点；未加入的房间先邀请/接受；只展示真实功能和加载/错误/解密失败恢复入口。
- 图片/音频/视频/文件，100000000实际上传字节校验、进度/取消/重试；限速和并发用session/服务端要求（当前上传速率500000字节/秒、服务器上限3，单队列不绕过）。音视频采用Media3，音频录制/通知/存储按Android版本渐进申请权限，Photo Picker/SAF避免泛存储权限。下载410过期、503服务故障分开；附件落盘与临时URI按账号清理，无权限不展示旧缓存。
- 收款/红包使用原生消息气泡，点击打开App内确认与详情；YIDT授权操作本身走系统浏览器。转账只有指定收款人主动receive；到账处理中不能显示已到账。收款回执用独立消息事务，资金操作重试不能变成第二次扣款。
- create/approve/fund/receive/claim分开。未知结果先查原订单；业务key与消息txn分开，持久化。`actions`及到账/退款以服务器为准，十进制字符串+BigInteger/BigDecimal；金额绝不Double/Float。红包至少0.01×份数，24小时期限，随机分配服务端执行。
- 新增cancel接口见当前API第16节：付款人只可取消未入金订单；确认后不fund、不发消息；已入金409，不拿取消当退款。响应丢失先查原id，重复同key返回cancelled原单。取消与fund竞态必须实测。
- 赠卡确认资产转移后独立发送消息，列表与聊天均显示真实卡面预览；消息失败不能再次赠送。以真实实例/模板/损耗和赠送时快照渲染。
- 预览只识别白名单，解密后只发送必要URL/room，消息进入可视区自动渲染，支持旧URL文字，无需先点击。共享缓存/并发限制/可见性暂停，不随Compose重组重复请求。订单仅可见时全局合并低频刷新，终态停止。
- 跳伞复用轻量场景布局与SVG素材，可在Compose Canvas移植；显示真实固定结果快照，不显示未公开crash_height，不自动下注/加入。
- YCard优先复用`static/ycards-render.js`真实效果。可用**仅加载包内可信只读渲染资源**的隔离渲染容器接入Compose：AndroidX WebKit WebViewAssetLoader，禁任意导航/外部HTML/文件访问，不注入token/私钥，不用整页iframe，不承担聊天、登录或付款。所有鉴权资源由原生Repository按权限下载，传入最小展示数据；移出视口暂停。若实现原生渲染，必须以真实模板/材质/光效/损耗对照验收，不能凭空画卡面。原站操作始终系统浏览器。安全本地资源参考：<https://developer.android.com/develop/ui/views/layout/webapps/load-local-content>。

## App持久备份/恢复与撤回

Web是临时使用，不参与自动包备份/在线成员补齐。App才提供长期保存、自动备份和恢复；它不能靠聊天归档恢复加密私钥。

1. 持久保存服务端认证摘要、原canonical字节（末尾LF）、cursor/until、水位和tombstone，水位仅整页/整窗口可靠提交后推进。增量从最后成功更新时间前5分钟，重新登录前10分钟；按服务器实际接受时间/sequence，不按可伪造origin_server_ts。
2. 30天内仍有正文的包必须服务器提供；只有服务器确认已清理、具备可信摘要/历史权限的包才申请App在线成员补齐。使用现有packets/exchange流程，不直接给任意用户上传数据、不P2P任意抓取。客户端声明“App”不是安全边界，权限仍由服务器校验。
3. 私聊成员只能交换原密文包，不能上传解密正文、私钥或整段聊天；群按原事件形式。接收者在本地对Base64解码的原canonical包做SHA256核验，匹配认证摘要且event/room/type一致后保存；哈希错误、未知来源或撤回内容拒绝显示。无在线持有者/缺历史密钥时明确无法恢复。
4. 撤回5分钟限制在服务端，特殊订单/赠卡不能撤回。接收更新即应用tombstone、清理可展示缓存；不重新投递为新Matrix消息，不篡改原始统一归档。备份/恢复时继续应用可信撤回元数据，不能借旧包复活已撤回正文。
5. App本地私有持久包库可先作为自动备份首层；另实现用户选择的SAF目录/外部加密备份任务、状态和恢复入口。备份加密容器可以用审计库的成熟AEAD/口令派生方案，不自创通信加密协议；格式版本化、摘要/完整性验证、先验证再导入本地，绝不重新发送历史。Matrix密钥备份按原生SDK另行引导/恢复，禁止明文上传私钥。Keystore不可导出，跨机恢复不能仅复制其密文包装文件，须有独立用户恢复途径。
6. 归档已有snapshot/两种封存规格、UTC日期、完整日权限、未封存/清理状态；SHA256与认证获得的可信摘要在本地对比，不上传用户文件/正文/密钥，不给实时消息加“已每日存证”徽章，不往统一封存文件加个人字段。App自动备份不是修改统一归档规格。
7. Android系统Auto Backup不要备份可被其他安装/账号误用的token/Keystore包装库；配置dataExtractionRules/backup规则。用户自主加密导出的消息备份与Matrix密钥备份要明确分开。

## APK交付和签名

固定包名建议`org.yitiao.yichat`；minSdk建议26，compile/target SDK按实际安装的受支持稳定版本固定并满足交付环境。JDK/AGP/Gradle/Kotlin/Compose BOM与SDK AAR都要版本目录锁定，提交Gradle wrapper及校验。先产arm64-v8a设备版和可测x86_64模拟器版，验证Rust .so打包与16KB页兼容；不能只交Android Studio工程。

APK使用**首次随机生成的专用自签名证书**，不使用已有生产签名/用户密钥；随机生成一次，后续版本复用，保持可原位升级。`prepare_signing.py` 提供安全生成辅助；keystore/密码不进Git、提示词、公开源码tar或日志。交付签名APK、SHA256、apksigner验签结果、证书SHA256及对应assetlinks配置。私有签名材料作为独立私有交付供用户保存，不和公开源码包混放；如果当前渠道不能安全交付，明确告知保存位置和取用方式，不印出密码。

官方签名依据：<https://developer.android.com/studio/publish/app-signing>。App Links证书绑定见上文；更换随机签名会使原位升级和链接校验失效，并可能因卸载丢失本地消息/密钥，因此不每次重签。
