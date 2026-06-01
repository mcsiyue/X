# X/Twitter 批量视频下载器
<img width="1442" height="1323" alt="9b20acc94b1e5e58a3ba9fa9ae89032f" src="https://github.com/user-attachments/assets/eacaa159-6508-40c0-b527-17a7b1eaf9fe" />

一个基于 Python Tkinter 的 Windows 桌面工具，用于批量解析并下载 `x.com` / `twitter.com` 帖子中的视频。

## 功能特性

- 支持批量粘贴 X/Twitter 帖子链接，一行一个。
- 新添加的任务会显示在任务列表最上方。
- 支持“添加队列后自动提取”：添加任务后自动解析标题、画质、直链和文件大小，但不会自动下载。
- 支持多线程并发下载，可在界面中设置并发线程数，默认 3 个线程。
- 自动读取并显示视频文件大小。
- 统计已完成下载的总文件大小。
- 下载失败的任务会自动标红。
- 支持一键重试失败/停止的任务。
- 支持点击表头排序，例如按下载进度、文件大小排序。
- 自动过滤非 X/Twitter 链接。
- 自动选择最高画质下载。
- 支持设置保存目录、失败重试次数。
- 下载任务列表展示状态、进度、画质、文件大小、标题、链接和文件路径。
- 支持打开下载文件、打开保存目录、查看错误日志。
- 点击窗口右上角关闭按钮时，默认收纳到系统托盘。
- 托盘操作：单击/双击显示窗口，右键显示菜单。
- 已支持自定义 exe 图标、窗口图标和托盘图标。

## 运行源码

推荐使用 Python 3.11+。

```powershell
python x_twitter_downloader.py
```

程序本身只依赖 Python 标准库。打包 exe 需要安装 PyInstaller：

```powershell
pip install pyinstaller
```

## 打包 exe

```powershell
pyinstaller --clean --noconfirm XTwitter批量视频下载器.spec
```

打包完成后，exe 会生成在：

```text
dist/XTwitter批量视频下载器.exe
```

## 使用提示

- “并发线程”建议设置为 2-5。过高可能受到网络或接口限速影响。
- “添加队列后自动提取”只做解析和读取大小，不会自动下载文件。
- 如果下载失败，可点击“重试失败”。
- 点击“文件大小”或“下载进度”表头可以排序。
- 下载失败时会在当前目录生成 `downloader_error.log`，可用于排查问题。
-
## 友链
linuxdo nodeseek
