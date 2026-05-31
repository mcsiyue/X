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

## 2. 本地运行

```powershell
python x_twitter_downloader.py
```

## 3. 打包 Windows exe

在项目根目录执行：

```powershell
pyinstaller --clean --noconfirm XTwitter批量视频下载器.spec
```

输出文件：

```text
dist/XTwitter批量视频下载器.exe
```

## 4. 功能验证建议

- 添加多条 X/Twitter 链接，确认新任务出现在列表顶部。
- 将“并发线程”设置为 2 或 3，点击开始下载，确认多个任务同时进入提取中/下载中。
- 下载时确认“文件大小”列能显示 KB/MB/GB。
- 点击关闭按钮，确认程序进入系统托盘；右键托盘图标可显示菜单。

## 5. GitHub 发布建议

```powershell
git init
git add .
git commit -m "Initial release"
git branch -M main
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

Release 中可上传打包好的：

```text
dist/XTwitter批量视频下载器.exe
```
