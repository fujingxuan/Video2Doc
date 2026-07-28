# Video2Doc Windows 便携版

## 发行形态

构建产物：

```text
dist\Video2Doc-Portable\
  Video2Doc.exe
  _internal\
  PORTABLE_README.txt

dist\Video2Doc-Portable.zip
```

这是 PyInstaller onedir 便携版。React production build、FastAPI 后端、Alembic 和迁移文件在同一发行目录中；FFmpeg、ffprobe、yt-dlp 和所选模型 Gateway 仍是外部依赖，避免把大型模型或用户已有 Docker 服务复制进应用。

## 启动与数据

双击 `Video2Doc.exe`。默认行为：

1. 创建 `%LOCALAPPDATA%\Video2Doc`；
2. 设置自己的 SQLite、任务和默认测试 Inbox；
3. 自动执行 Alembic upgrade 到 head；
4. 检查 `127.0.0.1:8765`，若被其他程序占用则选择后续空闲端口；
5. 启动 Uvicorn/FastAPI 并托管前端；
6. 健康后打开系统浏览器。

命令行选项：

```powershell
Video2Doc.exe --port 8877
Video2Doc.exe --no-browser
Video2Doc.exe --app-home D:\Video2Doc-AppData
Video2Doc.exe --app-home D:\Video2Doc-AppData --check-only
```

显式 `--app-home` 会覆盖继承的数据库/数据目录环境变量，便于隔离验收和诊断。默认双击启动时，端口被另一个 Video2Doc 占用会打开已运行实例，被其他程序占用会按顺序选择后续空闲端口。显式传入 `--port` 或设置 `VIDEO2DOC_PORT` 时采用严格端口语义，冲突会报错，不会悄悄改变用户指定值。

## 退出与进程边界

可关闭启动器窗口、按 `Ctrl+C`，或在“应用设置”点击“退出 Windows 应用”。后端关闭流程会：

- 停止 Worker；
- 终止由当前 Video2Doc 启动的媒体子进程树；
- 把未完成运行任务保存为可恢复状态；
- 释放应用端口。

应用不会停止、删除或重启用户独立管理的 Docker OCR 容器，不执行 Docker prune，也不删除未知镜像或 volume。

## 构建

在已完成 `scripts\setup.ps1` 的仓库中执行：

```powershell
Set-Location D:\Projects\Video2Doc
.\scripts\build_portable.ps1
```

如依赖已经安装，可使用 `-SkipInstall`。脚本会先构建前端，再用 PyInstaller 生成 onedir，写入便携版 README，最后生成 ZIP。

## 故障提示

- “发行包缺少前端/迁移资源”：发行目录不完整，应重新解压 ZIP。
- “数据库迁移失败”：查看 `<app-home>\logs\launcher-error.log`。
- 显式端口提示“被其他程序占用”：关闭冲突程序或改用另一个 `--port`。
- 媒体工具缺失：应用仍能启动；在设置页补充路径后重新检查。
- Gateway 缺失或不可用：任务结构化失败并保留错误/attempt，恢复服务后重试。

当前发行版使用系统浏览器，不是 MSI、自动更新器、系统托盘或独立 WebView。便携版可用性不依赖这些附加形态。
