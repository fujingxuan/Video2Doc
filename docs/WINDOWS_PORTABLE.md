# Video2Doc Windows 便携版

从 [GitHub Releases](https://github.com/fujingxuan/Video2Doc/releases/latest) 下载 Windows 便携 ZIP，完整解压后双击 `Video2Doc.exe`。运行界面无需安装 Python 或 Node.js；需要 Windows 10/11 和 Microsoft Edge。

## 发行形态

构建产物：

```text
dist\Video2Doc-Portable\
  Video2Doc.exe
  _internal\
  PORTABLE_README.txt

dist\Video2Doc-Portable.zip
```

这是 PyInstaller onedir 便携版。React production build、FastAPI 后端、Alembic 和迁移文件在同一发行目录中。必须保留整个解压目录，**不能单独复制或发布 `Video2Doc.exe`**。

本次公开 ZIP 不附带 FFmpeg、ffprobe 或 yt-dlp。请从 [FFmpeg 官方下载页](https://ffmpeg.org/download.html) 与 [yt-dlp 官方 Releases](https://github.com/yt-dlp/yt-dlp/releases) 获取相应工具，并放入 PATH；也可在“应用设置”填写 `ffmpeg.exe` 和 `yt-dlp.exe` 完整路径，将同一 FFmpeg 发行包的 `ffprobe.exe` 放在 `ffmpeg.exe` 旁边。

发行包不含 ASR/OCR 模型权重、WSL/Python 模型运行环境或 Docker 镜像。需要识别时，请按 [ASR 部署说明](MODEL_DEPLOYMENT_REPORT.md) 与 [OCR 部署说明](OCR_MODEL_DEPLOYMENT_REPORT.md) 部署本地 Gateway，或配置兼容的远程服务及自己的密钥。远程服务会收到任务对应的音频、图片或正文。只有需要文本整理时才需配置 LLM。

项目代码许可证见 [LICENSE](../LICENSE)，随包组件的声明见 [第三方声明](../THIRD_PARTY_NOTICES.md)；外部工具和模型遵循各自的许可条件。

## 启动与数据

双击 `Video2Doc.exe`。默认行为：

1. 创建 `%LOCALAPPDATA%\Video2Doc`；
2. 设置自己的 SQLite、任务和默认测试 Inbox；
3. 自动执行 Alembic upgrade 到 head；
4. 检查 `127.0.0.1:8765`，若被其他程序占用则选择后续空闲端口；
5. 启动 Uvicorn/FastAPI 并托管前端；
6. 健康后用 Microsoft Edge 应用模式打开独立 Video2Doc 窗口；该隔离窗口禁用扩展和扩展后台页，避免第三方插件首次运行页抢占窗口。
7. 使用 Windows 命名互斥锁保证整个登录会话中只有一个 Video2Doc 主进程和一个应用窗口；重复启动只唤醒已有窗口。

命令行选项：

```powershell
Video2Doc.exe --port 8877
Video2Doc.exe --no-browser
Video2Doc.exe --app-home D:\Video2Doc-AppData
Video2Doc.exe --app-home D:\Video2Doc-AppData --check-only
```

显式 `--app-home` 会覆盖继承的数据库/数据目录环境变量，便于隔离验收和诊断。应用已经运行时再次双击，第二个启动进程会尝试唤醒已有窗口后立即正常退出，不新建窗口，也不会关闭现有后端。主进程按标题精确匹配的真实顶层窗口句柄监控生命周期，不依赖 Edge 是否把新窗口进程挂在启动进程之下。默认端口被其他程序占用时会按顺序选择后续空闲端口。显式传入 `--port` 或设置 `VIDEO2DOC_PORT` 时采用严格端口语义，冲突会报错，不会悄悄改变用户指定值。

## 退出与进程边界

默认双击启动没有控制台窗口。点击独立 Video2Doc 窗口右上角 X，或在“应用设置”点击“退出 Windows 应用”。使用 `--no-browser` 从命令行启动时仍可按 `Ctrl+C`。后端关闭流程会：

- 停止 Worker；
- 终止由当前 Video2Doc 启动的媒体子进程树；
- 把未完成运行任务保存为可恢复状态；
- 释放应用端口。

应用不会停止、删除或重启用户独立管理的 Docker OCR 容器，不执行 Docker prune，也不删除未知镜像或 volume。

## 构建

在已完成 `scripts\setup.ps1` 的仓库中执行。默认构建不捆绑 yt-dlp，FFmpeg 和模型也不会由便携构建脚本下载或打包。

```powershell
Set-Location D:\Projects\Video2Doc
.\scripts\build_portable.ps1
```

如依赖已经安装，可使用 `-SkipInstall`。脚本会先构建前端，再用 PyInstaller 生成 onedir，写入便携版 README，最后生成 ZIP。

自行构建时可显式选择捆绑 yt-dlp：

```powershell
.\scripts\update_media_tools.ps1
.\scripts\build_portable.ps1 -BundleYtDlp
```

下载器脚本从 yt-dlp 官方 Release 获取 EXE，并核对官方 SHA-256 清单。再发行该自定义包时，需要同时准备该组件及其捆绑依赖要求的许可证、对应源代码等第三方合规材料，详见 [第三方声明](../THIRD_PARTY_NOTICES.md)。

## 故障提示

- “发行包缺少前端/迁移资源”：发行目录不完整，应重新解压 ZIP。
- “数据库迁移失败”：查看 `<app-home>\logs\launcher-error.log`。
- 无控制台便携版的启动与 Uvicorn 日志写入 `<app-home>\logs\launcher.log`。
- 页面提示“无法连接 Video2Doc 后端”：关闭旧版遗留的失效窗口，只保留并重新启动当前版本窗口。
- 显式端口提示“被其他程序占用”：关闭冲突程序或改用另一个 `--port`。
- 媒体工具缺失：应用仍能启动；在设置页补充路径后重新检查。
- 内置本地 ASR/OCR 不可用：任务会先按 Gateway 配置自动启动运行时；缺少环境、模型或启动组件时保留明确错误和启动日志，修复后可重试。
- 外部 Gateway 不可用：任务结构化失败并保留错误/attempt，恢复服务后重试。

独立窗口使用 Windows 自带的 Microsoft Edge 应用模式和隔离的 `%LOCALAPPDATA%\Video2Doc\browser-profile`，并禁用该窗口中的浏览器扩展。若系统找不到 Edge，启动器会明确报错，不会回退并额外打开默认浏览器窗口。当前发行版不是 MSI、自动更新器或系统托盘应用。
