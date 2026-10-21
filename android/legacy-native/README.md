# 旧原生客户端归档

0.1.0～0.1.4 的 Compose / Matrix Kotlin Rust FFI 聊天实现及测试，仅作追溯，不参与 Gradle 构建。0.2.0 共享网页聊天前端。历史 BROWSER-LOGIN-FIX / CALLBACK-FIX / SYNC-BACKUP-FIX / TLS-SYNC-FIX 报告描述旧版本，不能用来证明当前容器的测试结果。

迁移保留旧数据路径、Android Keystore 与 SecureStore 包装密钥。不要为“清理旧代码”删除用户 no_backup/accounts 或密钥；新网页设备不能重用旧 Rust 原生设备证明。
