# 下载逻辑说明

本文档记录当前版本的核心下载逻辑，方便 GitHub 发布、维护和二次开发。

## 版本

- 当前源码版本：v3.6
- 主程序：`x_twitter_downloader.py`
- GUI 框架：Tkinter
- 网络下载：Python 标准库 `urllib.request`
- 并发：`threading` + `queue` + `concurrent.futures.ThreadPoolExecutor`
- 打包：PyInstaller 单文件 exe

## 总体流程

1. 用户在 GUI 中批量粘贴链接。
2. 程序过滤支持的域名。
3. 每个链接生成一个 `DownloadTask`。
4. 如果开启“添加队列后自动提取”，后台线程会先解析视频信息。
5. 点击开始下载后，程序按界面设置的任务并发数处理队列。
6. 每个任务先解析候选视频，再选择默认最高画质下载。
7. 下载进度、状态、文件大小通过 UI 队列回传到主线程刷新界面。
8. 下载失败时写入 `downloader_error.log`，任务行标红，可重试。
9. 批量下载运行中继续添加的新任务会自动追加到当前下载队列，已有任务完成后继续处理新任务。

## 支持域名

源码中的 `SUPPORTED_HOSTS` 包含：

```text
x.com
www.x.com
twitter.com
www.twitter.com
m.twitter.com
mobile.twitter.com
pornhub.com
www.pornhub.com
cn.pornhub.com
m.pornhub.com
truvaze.com
www.truvaze.com
```

PornHub 域名单独由 `PORN_HUB_HOSTS` 判断。


## 站点识别逻辑

函数：`detect_video_site()`

添加链接时会根据 URL hostname 自动识别站点：

- `x.com` / `twitter.com` / 移动端域名 → `X/Twitter`
- `pornhub.com` / `cn.pornhub.com` / 移动端域名 → `PornHub`
- `truvaze.com` / `www.truvaze.com` → `Truvaze`
- 其它域名在未开启过滤时会显示原始 hostname，方便排查链接来源。

识别结果保存在 `DownloadTask.site`，并显示在任务列表“站点”列。

## X/Twitter 解析逻辑

X/Twitter 使用第三方解析接口：

```text
https://x-twitter-downloader.com/api/parse-video
```

流程：

1. 对用户输入的 X/Twitter 链接构造请求。
2. 设置常见浏览器请求头和 `Referer`。
3. 调用解析接口。
4. 从返回 JSON 中读取候选视频信息。
5. 生成 `VideoOption` 列表。
6. 使用 `choose_best_option()` 默认选择最高画质。
7. 使用 `_download_direct_video()` 进行流式下载。

## Truvaze 解析逻辑

Truvaze 视频页面通常直接包含 Twitter 视频 CDN 的 mp4 地址。

流程：

1. 请求 Truvaze 视频详情页，例如 `/zh-CN/movie/<id>`。
2. 优先解析页面中的 JSON-LD `VideoObject`。
3. 从 `contentUrl` / `embedUrl` / `url` 字段提取 `video.twimg.com` mp4 直链。
4. 兜底扫描 HTML 中出现的 `https://video.twimg.com/...mp4` 地址。
5. 如果 URL 文件名包含当前 movie id，则优先只保留当前视频，避免误选相关推荐视频。
6. 从 mp4 URL 路径中的 `1080x1440` 等字段推断清晰度和宽度。
7. 后续复用直链下载逻辑下载文件。

## PornHub 解析逻辑

PornHub 不使用账号、Cookie 或浏览器凭据，主要从公开视频页面 HTML 中提取页面内已有的视频配置。

流程：

1. 请求 PornHub 视频页面 HTML。
2. 在 HTML 中查找 `flashvars_...` 或 `flashvars` 变量。
3. 从 `mediaDefinitions` 提取候选视频地址。
4. 支持的字段包括：
   - `videoUrl`
   - `video_url`
   - `url`
5. 候选地址可能是：
   - mp4 直链
   - m3u8 playlist
6. 生成 `VideoOption` 列表。
7. 默认选择最高画质。

## 默认最高画质选择规则

候选视频使用 `option_score()` 排序，核心维度：

1. 清晰度高度，例如 1080、720、480。
2. 视频宽度。
3. 同清晰度下优先 mp4/直链，避免不必要的 m3u8 分片下载。
4. 码率或质量描述中的数字。

也就是说：

- 不会为了 mp4 直链牺牲更高分辨率。
- 分辨率相同或接近时，优先下载更省时的直链。
- 没有直链时，自动走 m3u8 分片下载。

## mp4 直链下载逻辑

函数：`_download_direct_video()`

流程：

1. 创建目标文件路径。
2. 使用 `.part` 临时文件下载。
3. 请求视频直链。
4. 每次读取 `1MB` 数据块写入临时文件。
5. 根据 `Content-Length` 更新进度。
6. 下载完成后使用 `os.replace()` 把 `.part` 替换为正式文件。
7. 失败时删除临时文件并按设置重试。

## m3u8 下载逻辑

函数：`_download_m3u8_video()`

### playlist 解析

1. 请求 m3u8 文本。
2. 如果是 master playlist，选择最高带宽/最高分辨率的 variant。
3. 如果 variant 仍然是 m3u8，继续向下解析。
4. 最多允许 4 层嵌套，避免异常循环。
5. 最终得到所有视频分片 URL。

### 并发分片下载

当前版本已将 m3u8 从串行下载优化为并发下载。

配置：

```python
PORN_HUB_M3U8_WORKERS = 8
```

流程：

1. 为当前任务创建临时分片目录。
2. 使用 `ThreadPoolExecutor(max_workers=8)` 并发下载分片。
3. 每个分片单独保存为类似 `000001.part` 的临时文件。
4. 每个分片最多重试 3 次。
5. 主线程通过 `wait(..., timeout=0.25)` 短间隔轮询收集完成的分片，停止下载时可以更快响应。
6. 按已完成分片数更新进度，下载分片阶段只推进到 90%。
7. 所有分片下载完成后，界面切换为“合并分片 x/y”，合并阶段从 90% 推进到 99%。
8. 合并完成后显示“写入最终文件”，再使用 `os.replace()` 输出正式 `.mp4` 文件。
9. 任务真正完成后才显示 100% 和“已完成”。
10. 临时分片目录由 `TemporaryDirectory` 自动清理。

### 为什么 m3u8 会慢

m3u8 视频由大量小分片组成。旧版本一次只下载一个分片，网络延迟会被放大。v3.0 改成多个分片同时下载，能明显减少等待时间。

仍可能影响速度的因素：

- 视频 CDN 本身限速。
- 网络到目标 CDN 节点质量差。
- 分片数量过多且单个分片很小。
- 目标站点临时返回慢速节点或失败节点。
- 本地磁盘写入速度或杀毒软件扫描影响。

## 文件命名和临时文件

- 正式文件名来自视频标题，并经过非法字符清理。
- 下载中使用 `.part` 后缀。
- 下载失败时会尽量删除 `.part`。
- 如果文件名冲突，会自动生成不冲突的新文件名。

## 日志

错误日志文件：

```text
downloader_error.log
```

日志中包含：

- 任务 URL
- 直链或 m3u8 URL
- 当前重试轮次
- Referer
- 异常信息和 traceback

## 单文件 exe 说明

`XTwitter批量视频下载器.spec` 使用 PyInstaller 构建单文件 exe。发布给普通用户时，只需要提供：

```text
dist/XTwitter批量视频下载器.exe
```

用户电脑不需要安装 Python。Python 运行时和程序资源会被打包进 exe 内部。

## 动态下载队列

从 v3.6 开始，批量下载线程使用持久下载队列：

- 点击“开始下载”后，会把当前未完成任务加入下载队列。
- 下载运行中继续添加链接，新任务会自动进入当前下载队列。
- 工作者线程在队列暂时为空后会短暂等待，方便接收刚添加的新任务。
- 点击“停止”后不再继续处理新任务。


## 进度、大小和速度显示

从 v3.6 开始：

- 自动提取或下载响应头扫描到文件大小后，会立即写入任务的“文件大小”列。
- “下载进度/大小”列显示百分比和 `已下载大小/总大小`。
- 新增“下载速度”列，按最近传输增量计算当前速度。
- m3u8 如果无法提前知道总字节数，会显示已下载大小，分片阶段仍按分片完成数推进。

## 停止下载优化

从 v3.6 开始：

- 点击停止后立即设置全局停止事件。
- 尚未开始的排队任务会立即标记为“已停止”。
- m3u8 分片等待从 `as_completed()` 阻塞等待改为短间隔轮询 `wait(..., timeout=0.25)`。
- 直链、m3u8 playlist、m3u8 分片请求使用更短的网络超时，避免卡住很久才响应停止。


## v3.6 交互调整

- 失败重试次数默认值改为 5。
- 删除“自动选择最高画质”复选框，程序始终使用 `choose_best_option()` 自动选择最高画质。
- 任务列表上方新增“重新下载失败视频”按钮，调用 `retry_failed_tasks()`。
- 右键菜单新增“删除已下载视频文件”，会删除选中任务对应的本地文件，并把任务标记为已停止，方便后续重新下载。

