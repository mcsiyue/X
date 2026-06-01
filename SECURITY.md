# Security Policy

如果你发现安全问题，请不要公开提交可被滥用的细节。建议通过 GitHub 私密渠道或 Issue 中的概述方式联系维护者。

## 数据和凭据说明

本项目是桌面下载工具：

- 不会主动收集账号、密码、Cookie 或浏览器凭据。
- 不需要用户登录目标站点。
- 不读取浏览器 Cookie 数据库。
- 不上传本地文件。
- 下载失败时只在本地生成 `downloader_error.log`。

## 网络请求说明

- X/Twitter 链接会请求 `x-twitter-downloader.com` 的解析接口。
- PornHub 链接会请求公开视频页面，并从页面内已有的 `flashvars` / `mediaDefinitions` 提取视频地址。
- 下载视频时会请求解析得到的视频直链或 m3u8 分片地址。

## 发布版说明

PyInstaller 打包后的 exe 是单文件发布版。普通用户运行 exe 不需要安装 Python。
