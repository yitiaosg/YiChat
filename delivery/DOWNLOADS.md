# 下载与校验

- [arm64-v8a APK](https://github.com/yitiaosg/YiChat/raw/refs/tags/android-20261020-artifacts.2/apk/YiChat-0.1.0-2-arm64-v8a.apk)
- [x86_64 APK](https://github.com/yitiaosg/YiChat/raw/refs/tags/android-20261020-artifacts.2/apk/YiChat-0.1.0-2-x86_64.apk)
- [完整交付文件 ZIP](https://github.com/yitiaosg/YiChat/archive/refs/tags/android-20261020-artifacts.2.zip)
- [原交付源码 tar.gz](https://github.com/yitiaosg/YiChat/raw/refs/tags/android-20261020-artifacts.2/YiChat-source-20261020.tar.gz)
- [逐文件 SHA256SUMS](https://github.com/yitiaosg/YiChat/blob/android-20261020-artifacts.2/SHA256SUMS)

完整 ZIP 为 GitHub 自动打包的固定制品标签，与原始交付 ZIP 的外层文件名/封装不同；APK、源码 tar.gz、报告和截图的原始字节未改。解压进入目录后运行 `sha256sum -c SHA256SUMS`。

单独下载两个 APK 时，使用 [APK-SHA256SUMS](APK-SHA256SUMS)。签名证书与原交付相同，升级时复用同一专用私钥；私钥及密码不在 GitHub 上。

这是集成候选版，正式发布限制见 [验证报告](../VERIFICATION-ANDROID.md)。
