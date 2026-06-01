# 月之视频下载器

一个 Windows 桌面批量视频下载工具，基于 Python Tkinter 编写，可打包成单文件 exe 发布。用户使用发布版 exe 时不需要安装 Python。

## 功能特性

- 批量粘贴链接，一行一个。
- 自动识别链接所属站点，并在任务列表“站点”列标注，例如 `X/Twitter`、`PornHub`、`Truvaze`。
- 新添加的任务显示在任务列表最上方。
- 支持“添加队列后自动提取”：添加任务后自动解析标题、画质、直链和文件大小，但不会自动开始下载。
- 支持多任务并发下载，可在界面中设置并发线程数，默认 3 个任务线程。
- 默认始终使用最高画质下载，界面不再提供关闭最高画质的选项。
- PornHub 同清晰度下优先选择 mp4 直链；无直链时下载 m3u8。
- Truvaze 页面通过 JSON-LD `VideoObject.contentUrl` 和页面中的 `video.twimg.com` mp4 直链提取视频。
- PornHub m3u8 分片启用并发下载，默认 8 个分片线程。
- PornHub 下载状态会显示“解析 m3u8 列表 / 下载分片 x/y / 合并分片 x/y / 写入最终文件”。
- 下载运行中继续添加的新任务会自动加入当前下载队列。
- 下载进度列显示“已下载大小/总大小”，并新增“下载速度”列。
- 停止下载响应优化：会立即取消排队任务，并更快中断 m3u8 分片下载等待。
- 自动读取并显示视频文件大小。
- 统计已完成下载的总文件大小。
- 下载失败的任务会自动标红。
- 新增“重新下载失败视频”按钮，支持一键重试失败/停止的任务。
- 支持点击表头排序，例如按下载进度、文件大小排序。
- 支持设置保存目录、失败重试次数。
- 下载任务列表展示站点、状态、进度、速度、画质、文件大小、标题、链接和文件路径。
- 支持打开下载文件、打开保存目录、查看错误日志。
- 右键菜单支持删除已下载的视频文件。
- 点击窗口右上角关闭按钮时，默认收纳到系统托盘。
- 托盘操作：单击/双击显示窗口，右键显示菜单。
- 支持自定义 exe 图标、窗口图标和托盘图标。

## 下载逻辑概览

详细说明见 [`DOWNLOAD_LOGIC.md`](DOWNLOAD_LOGIC.md)。

简要流程：

1. 根据链接域名判断下载类型。
2. X/Twitter 链接调用 `x-twitter-downloader.com` 的解析接口获取视频候选项。
3. PornHub 链接请求视频页面 HTML，从 `flashvars` / `mediaDefinitions` 中提取候选视频地址。
4. 对候选项按清晰度、宽度、直链优先级和码率排序，默认选择最高画质。
5. mp4 直链使用普通流式下载。
6. m3u8 链接先解析 master/variant playlist，再并发下载所有视频分片，最后按序合并成 mp4。

## 运行源码

推荐使用 Python 3.11+。

```powershell
python x_twitter_downloader.py
```

源码运行只使用 Python 标准库。打包 exe 需要安装 PyInstaller：

```powershell
python -m pip install pyinstaller
```

## 打包单文件 exe

```powershell
pyinstaller --clean --noconfirm XTwitter批量视频下载器.spec
```

打包完成后，exe 会生成在：

```text
dist/XTwitter批量视频下载器.exe
```

发布给普通用户时，只需要分发这个 exe。用户电脑不需要安装 Python。

## 使用提示

- “并发线程”控制同时处理的任务数量，建议设置为 2-5。
- PornHub m3u8 内部分片并发数在源码中由 `PORN_HUB_M3U8_WORKERS` 控制，默认 8。
- “添加队列后自动提取”只做解析和读取大小，不会自动下载文件。
- 如果下载失败，可点击“重试失败”。
- 点击“文件大小”或“下载进度”表头可以排序。
- 下载失败时会在当前目录生成 `downloader_error.log`，可用于排查问题。

## 项目文件说明

```text
x_twitter_downloader.py          主程序源码，包含 GUI、解析逻辑和下载逻辑
XTwitter批量视频下载器.spec      PyInstaller 单文件打包配置
DOWNLOAD_LOGIC.md                下载逻辑说明
BUILD.md                         构建和发布说明
CHANGELOG.md                     更新记录
SECURITY.md                      安全说明
推特下载器图标.ico               exe/窗口图标
推特下载器图标_透明.png          托盘/资源图标
assets/                          图标资源备份
```

## 友链
[Linux.do](https://linux.do/)

