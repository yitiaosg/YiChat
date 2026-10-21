> 历史原生候选版资料。0.2.0 已改为独立 WebView，共用聊天网页；当前架构、部署、安全边界和实际测试请先看根目录 WEB-APP-REFACTOR.md / ARCHITECTURE.md。

# 本地数据与边界

- UID、用户名、MXID、device_id分别存储；SID只由网关验证，不把设备ID当SID。网关 Bearer及设备证明使用 Android Keystore AES-GCM包装；不读取浏览器cookie、不接收用户粘贴token。
- SDK完整官方 UniFFI AAR版本26.10.15，源提交97a1589b70bd122c1d66e0d68ea008d8554076a5，Maven AAR SHA256 c0d9c0f6726bdfced0658c3f73bc09a090fb229204aadc1b5d02903ab61b7d27。所有SDK通信使用网关，正式release homeserver是https://chat.yitiao.org。
- 账号设备目录按聊天域名+UID+MXID+device哈希隔离；文件锁及单例Repository控制单写者。SDK SQLite用独立随机32字节存储密钥；App数据另用独立随机AES密钥。两者由Keystore包装保存。Room不是全库加密：表结构、room/event索引、sequence、摘要和操作状态仍可见；正文包、认证元数据和业务payload加密。
- SDK durable send queue负责稳定txnId与失败重试；业务key独立。需要外层引用的消息先暂停房间队列，持久记录已知SDK事务集合和引用，再取得新txn并登记；重启恢复绑定后才恢复队列。多个候选时拒绝猜测/重发。
- 加密发送前通过SDK完整state同步、成员获取、身份查询及初始化等待检查双方joined；禁止加密模式不一致或私聊明文降级。仍需真实设备/E2EE验收，构建通过不能替代它。
- 本地附件加密采用Matrix v2规定的AES256-CTR及密文字节SHA256，描述/密钥留在SDK加密消息内；外层只关联文件ID。接收由SDK鉴权下载、校验和解密。文件实际计数≤100000000，单上传队列限制500000字节/秒，服务端额度/并发保护继续执行。HTML/SVG/脚本不在附件查看器执行。
- 登录/支付均为系统浏览器 Intent。唯一WebView是包内YCard只读渲染器；WebViewAssetLoader只供appassets本地脚本，禁止任意导航/网络/文件访问/桥接，不注入认证信息；卡面图片来自经过原站验证的嵌入式data资源。
- 普通时间使用系统时区，存储和归档UTC。原canonical包按含末尾LF的原字节校验；可信摘要来自已认证服务器，不能采用peer自报hash。已撤回包先写tombstone并删除可展示原包。
- 外部备份格式YCB1，scrypt(N32768,r8,p1,随机32字节盐)+AES256-GCM(随机12字节nonce)，JCA与BouncyCastle实现。不是通信加密。备份不含网关会话、设备证明、SDK私钥或Keystore包装文件。恢复需独立用户口令，并重新核对服务器认证摘要/撤回；Matrix密钥备份独立。
- Android Auto Backup及device transfer排除App数据；用户SAF加密导出是跨设备途径。源码与公开交付包不包含签名材料或测试运行凭据。
