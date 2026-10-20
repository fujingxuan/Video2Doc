# 3.4.1 公开发行验证

日期：2026-10-21。公开版本保留 3.4.1 的应用功能，将 FFmpeg、ffprobe 与 yt-dlp 作为外部工具，并加入 MIT 许可证、贡献与安全说明、第三方许可归档。

## 发布验证

- 后端：222 passed，2 skipped；跳过的两项为需要显式启用的真实模型验收，本次没有重新执行模型推理。Ruff 通过。
- 前端：13 项 Vitest 测试、TypeScript 类型检查和 Vite 生产构建通过。
- 源码安装：修复仅有 python.exe 时的 PowerShell 数组解包问题，验证 Python 命令解析与安装失败守卫。
- 发行包：Windows x64 PyInstaller onedir，独立数据目录的迁移检查退出码为 0；真实 EXE 健康接口为 3.4.1、数据库正常、Worker 运行，任务和队列为空，首页 HTTP 200。
- 浏览器：实际页面显示 3.4.1，控制台错误为 0；通过该隔离实例的 shutdown API 正常退出，进程退出码为 0。本次没有重新验收 Edge 原生窗口关闭按钮或真实模型转换。
- ZIP：473 个文件与运行目录逐一 SHA-256 一致，CRC 校验通过；无用户数据库、任务、Cookie、日志或媒体文件。certifi 的公开 CA 证书保留用于 HTTPS。
- 许可：225 项组件记录与 252 份许可文本；实际 PyInstaller TOC 引用的 38 个已安装 Python 发行包均有许可归档。清单刻意包含部分构建组件，详细版本、来源和许可哈希见根目录 THIRD_PARTY_NOTICES.md。

## 下载

资产：`Video2Doc-3.4.1-windows-x64.zip`，36,178,812 字节。

SHA-256：`e40b3ae8a95c2000acd56dc01778cecca83311764216e0f4c49d07ee20defbb2`。

请完整解压后运行 `Video2Doc.exe`。媒体工具、ASR/OCR 模型与其独立环境需要另行安装；云端服务需用户自己的账号和密钥。[发行页面](https://github.com/fujingxuan/Video2Doc/releases/tag/v3.4.1) 的 `SHA256SUMS.txt` 为本次公开 ZIP 的校验文件。
