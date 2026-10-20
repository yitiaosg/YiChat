# 未入金订单取消与Android交接（2026-10-20）

确认转账/红包页面以及YIDT授权返回弹窗新增“取消订单 → 确认取消订单”。“保留订单”只返回确认前状态；关闭弹窗不代表取消。真正取消调用新业务接口，返回cancelled后终结本地操作，不入金、不发送气泡，下一次明确发起操作使用新业务key。去除旧“仅本地保留未入金订单后开始新操作”入口；历史已保留订单仍可打开原URL处理，不删除记录。

## 服务端

`POST /api/orders/{id}/cancel {}`：需要有效SID、当前房间成员、冻结的付款人UID。资金关停时仍能放弃未入金订单。与fund共用原SQLite账本BEGIN IMMEDIATE事务：只有尚未入金订单可取消，pending/approved YIDT intent同步作废；取消不会写入金交易、escrow、出金/退款队列或Matrix消息。原YIDT批准流程本身也在同一账本BEGIN IMMEDIATE下检查intent状态，因此旧授权页面不能复活已取消订单。

取消重复调用返回原结果；原创建key重试仍返回原cancelled订单。已入金取消409，已取消fund409；fund/cancel竞态只有一个成功。没有schema或Synapse模块迁移，不改本金/参与者/授权等不可变字段，不删除财务审计记录。完整接口见CHAT-KERNEL-API第16节。

## 客户端

actions.cancel驱动按钮，取消需明确二次确认。响应丢失先排空共享在途GET再查原订单，只有服务器cancelled才完成本地cancelled状态；失败不重建、不自动fund、不发消息。取消成功关闭当前操作弹窗，旧订单URL仍可查看已取消状态，停止订单轮询、无授权/入金按钮。已入金订单按原收款/领取/24小时退款规则处理。取消与发送消息使用不同流程，cancel分支没有announce调用。

## 测试与限制

- 资金层新增未批准/已批准×私聊/群聊取消、重复原key、重复取消、其他用户拒绝、已退出/离群拒绝、资金关闭仍可取消、取消与fund并发，以及已入金拒绝取消测试。全部使用临时权威账本和专用测试PG，未用真实资金/资产。
- 双账号Chromium浏览器真实点击群红包未授权取消、私聊YIDT原授权表单批准后返回聊天取消；点“保留订单”回退、手机390px无横向溢出、取消响应故意丢失后GET核实、原授权页拒绝、原fund409、重复cancel200、非付款人403、没有新增send请求、可重新发起新操作、刷新旧URL仍取消且无入金按钮。
- 首轮全套107项pytest通过（含33项真实浏览器检查），前端5项单元测试、严格TypeScript/Vite构建通过。最终复核及部署结果见本文件末尾。
- 手机是Chromium视口实际点击，不是Android真机；生产仅匿名/只读HTTPS验证，不对真实账户试转账。

## 构建部署与回滚

沿用WEB-DEPLOYMENT.md构建、备份和干净工作树fast-forward。当前增量只需原子发布静态资源并重启yichat，其他服务保持运行。新cancelled值不需要表结构迁移。源码与静态需配套，勿把旧UI绑定到只回滚一半的接口。

发布前源码回滚基线：`0979aaf`，静态：`/var/lib/yichat/web/releases/20261020T031315Z`。发布前私有备份：`/root/yitiao/backups/yichat-v2-20261020T034440Z/`。回退前确认没有未提交改动、做新备份，切换到基线源码并将current原子链接切回旧静态版本，重启网关；不要恢复旧数据库覆盖新消息/订单/出金队列，也不要清浏览器加密库。

旧代码不识别cancelled专属文案，但仍因state不等awaiting_deposit拒绝实际fund；已取消授权仍不能批准。回退后新订单取消入口消失，先记录这一行为变化，必要时仅关停创建/入金，不改已取消记录。

## Android交接材料

`yichat/handoff/` 包含文件地图、完整新会话提示词、原生架构和待补登录/Sliding Sync适配说明、隔离测试bootstrap、额外依赖、随机专用签名辅助及仅导出已跟踪文件的打包工具。源码包排除生产DB/env/DSN/私钥/登录cookie/用户上传和聊天归档、依赖和构建产物、旧Telegram、Git历史；人工合成归档样例可保留。导出副本移除两处凭据默认值，记录到manifest，不改写生产主站配置。

新增测试安全检查只接受本机yichat_archive_test与yc_test_*角色，避免误清生产schema；测试拷贝yidt_core改从当前源码相对路径，供新Cloud环境使用。

下一会话才开发App和APK，本轮不生成Android工程、不部署未实现的原生登录/推送。App登录/钱包等用系统浏览器；APK首次随机专用自签名、后续复用，私有签名材料独立保管，不能装进源码包。

## 最终复核与发布结果

- 最终UI/测试安全与相对路径修改后再次跑全套：107项pytest通过，含33项浏览器检查、0个未捕获页面异常；额外确定性已入金拒绝用例加入后，资金专项29项全部通过。前端5项单元、严格TypeScript/Vite构建、Python编译和diff检查通过。
- 实现提交 `28e1275`，生产干净工作树fast-forward；静态release `/var/lib/yichat/web/releases/20261020T034732Z`，只重启yichat。生产未公开OpenAPI；以无副作用GET核实cancel路径返回405且Allow为POST；首页实际JS/CSS与构建匹配、WASM MIME/缓存正确、API/HTML no-store；6项服务active，health=ok。
- 生产匿名HTTPS桌面/390px手机页面真实点击主站登录，无横向溢出/JS异常；旧订单URL仍303返回聊天。只读对账全部通过，归档healthy、gaps=0；备份中的11个私有配置/密钥文件摘要全部未变，journal无warning。线上原有订单由真实用户产生，本次未创建或使用真实资金/资产。
- 测试截图：`tests/browser/artifacts/chat-cancel-group.png`、`chat-cancel-direct.png`、`chat-cancelled-group.png`、`chat-cancelled-direct.png`；不打入源码包。源码包按manifest导出，另提供SHA256及新会话提示词。

独立解压目录复核发现低频页面卸载后scroll事件仍引用已置空的ref，出现scrollHeight异常；已改用该事件自身currentTarget，并忽略已脱离文档的节点。浏览器退出用例额外对已卸载timeline派发scroll验证，不再读取空ref。该修复只影响页面生命周期，不改资金或加密协议。

最终修复版重新导出到独立目录，从包内源码离线npm ci、5项前端单元、严格TypeScript/Vite构建全部通过；构建入口及JS/CSS/WASM共5个文件与审核构建逐字节一致。独立目录全套pytest **109通过、4项依赖弃用warning，242.15秒**，含33项真实浏览器检查、0个未捕获页面异常；新增已卸载容器scroll检查通过。

滚动修复实现提交 `532806d`，生产最终静态版本 `/var/lib/yichat/web/releases/20261020T040117Z`，发布前备份 `/root/yitiao/backups/yichat-v2-20261020T040034Z/`。只重启网关；最终6服务、HTTPS桌面/手机登录入口、静态资源/WASM、只读对账均通过，11个私有配置/密钥摘要未变，journal无warning。只撤回该滚动修复时可回到 `338d49a` 与 `/var/lib/yichat/web/releases/20261020T034732Z`（仍保留取消功能），不恢复数据库覆盖实时数据。

最终交接tar仅在上述已验证源码上更新这份结果文档后生成；打包后再次校验文件摘要并确认代码文件与已验证导出包一致。Android App/系统浏览器原生登录交接/自动备份及APK仍由下一会话实现，不能把本次Web实测当作Android真机验收。
