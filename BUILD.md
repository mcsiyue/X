# 构建和发布说明

## 1. 准备环境

推荐环境：

- Windows 10 / Windows 11
- Python 3.11+
- PyInstaller 6+

安装 PyInstaller：

```powershell
python -m pip install --upgrade pip
python -m pip install pyinstaller
```

## 2. 本地运行源码

```powershell
python x_twitter_downloader.py
```

## 3. 语法检查

```powershell
python -m py_compile x_twitter_downloader.py
```

## 4. 打包 Windows 单文件 exe

在项目根目录执行：

```powershell
pyinstaller --clean --noconfirm XTwitter批量视频下载器.spec
```

输出文件：

```text
dist/XTwitter批量视频下载器.exe
```

这个 exe 是单文件发布版，普通用户不需要安装 Python。

## 5. 发布目录建议

建议 GitHub Release 上传：

```text
dist/XTwitter批量视频下载器.exe
```

也可以新建一个发布目录，只放：

```text
XTwitter批量视频下载器.exe
使用说明.txt
```

## 6. 功能验证建议

- 确认默认重试次数为 5，且界面不再显示“自动选择最高画质”复选框。
- 人为制造失败任务后，点击“重新下载失败视频”确认可以重试。
- 对已下载任务右键选择“删除已下载视频文件”，确认本地文件被删除。
- 添加多条 X/Twitter 链接，确认新任务出现在列表顶部。
- 添加 X/Twitter、PornHub 和 Truvaze 视频链接，确认“站点”列能正确显示 `X/Twitter` / `PornHub` / `Truvaze`。
- 添加 Truvaze 视频链接，确认可以提取 `video.twimg.com` mp4 直链并显示文件大小。
- 添加 PornHub 视频链接，确认可以提取标题、画质和直链/m3u8。
- 确认 PornHub 默认选择最高画质。
- 下载 PornHub m3u8 视频，确认状态依次显示“解析 m3u8 列表 / 下载分片 / 合并分片 / 写入最终文件”。
- 将“并发线程”设置为 2 或 3，点击开始下载，确认多个任务同时进入提取中/下载中。
- 下载时确认“文件大小”列能显示 KB/MB/GB。
- 点击关闭按钮，确认程序进入系统托盘；右键托盘图标可显示菜单。

## 7. GitHub 发布建议

```powershell
git init
git add .
git commit -m "Release v3.0"
git branch -M main
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

Release 说明可引用：

- 新增 PornHub / cn.pornhub.com 支持。
- 默认最高画质下载。
- PornHub m3u8 分片并发下载优化。
- PornHub 下载进度显示当前阶段。
- 下载运行中继续添加任务会自动排队。
- 单文件 exe 发布，用户无需安装 Python。
