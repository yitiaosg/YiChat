# Android 实际基础运行

环境：无KVM的软件模拟器。新版API35 Google APIs镜像启动过慢，系统Watchdog在App启动前重启system_server，因此不能把它列为现代WebView聊天验收。

改用已能完成启动的API28 AOSP x86_64，内置WebView为66.0.3359.158：

- 安装当前debug APK和instrumentation APK成功；保留原安装数据，未卸载。
- WebShellTest运行器报告“OK (4 tests)”，无失败。其中1项现代WebView的JS桥/IndexedDB完整页面测试因引擎条件不满足跳过；实际执行3项。
- 实际通过：离线APK内完整框架/JS/CSS/WASM读取，不缓存/api/session；原消息包内部AES-GCM快照及令牌/设备证明不进入备份、原数据命名空间保留；旧引擎显示明确更新入口，点击产生系统浏览器Intent。
- android-old-engine-update.png为真实模拟器截图。其他web-app-*截图为Playwright隔离真实聊天页面，两者不可混同。
- 当前release APK的同签名覆盖安装和启动结果见release-install.log。

现代Android System WebView下的实际登录回App、真实源消息桥、SAF目录/保存窗口写入、麦克风及厂商后台任务仍需真机人工确认；本次未做全量业务测试。双账号JS SDK真实同步/E2EE/刷新/断网重开由另列8项浏览器隔离检查证明，不替代上述Android系统集成验收。
