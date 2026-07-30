# Video2Doc 2.2.1 链接字幕与单窗口补丁验收报告

验收日期：2026-07-30

## 问题与原因

### OneAPI 提示

现场截图同时出现“`ONEAPI_API_KEY` 已配置”和
“`Text editing requires a configured OneAPI Base URL`”。API Key 与 Base URL
本来就是两项独立配置：密钥存在不代表服务地址存在。2.2.1 在设置页分别显示
API Key、Base URL 状态，并在创建任务前以中文逐项检查文本模型、API Key 和
Base URL。

### 字幕语义

2.2.0 把“导入字幕”实现为选择本地 SRT/VTT/ASS/SSA/TXT 并复制到任务工作区，
与需求“从视频链接直接抓取字幕”不一致。2.2.1 改为仅在视频网页 URL 模式下
使用 yt-dlp：

1. 读取媒体元数据中的人工字幕和自动字幕清单；
2. 按“人工中文 → 自动中文 → 人工英文 → 自动英文”选择一个精确语言代码；
3. 只下载这一条字幕，写入当前任务 `00_source/subtitles`；
4. 记录语言、人工/自动来源、格式、大小、SHA-256 和清单；
5. 解析后进入既有 normalized transcript、融合、Document 和 Markdown 链路，
   不调用 ASR。

早期真实 YouTube 验收暴露出宽泛 `zh.* / en.*` 会一次请求多条自动翻译字幕并
触发 HTTP 429。最终实现不再使用宽泛下载表达式，而是先选出一个精确语言代码；
对应回归测试确认传给 yt-dlp 的 `--sub-langs` 只有单个值。

### 重复窗口

2.2.0 通过启动 Edge 进程的后代关系寻找应用窗口。Edge 可把应用模式窗口转交给
已经存在的 profile 进程，导致启动进程与真实窗口不再保持父子关系，窗口监控和
单实例生命周期可能失配。2.2.1 使用两层约束：

- Windows 命名互斥锁只允许一个 Video2Doc 主进程；
- 按标题精确匹配真实顶层 `Video2Doc` 窗口句柄，重复启动只尝试唤醒已有窗口，
  不再打开默认浏览器作为回退。

## 自动化与真实验收

| 验证项 | 结果 |
| --- | --- |
| 后端全量测试 | `93 passed, 2 skipped, 1 warning` |
| 启动器与链接字幕针对性测试 | `15 passed, 1 warning` |
| Ruff | `All checks passed!` |
| 前端 | Vitest `1 passed`；TypeScript 通过；Vite production build 通过 |
| PowerShell | `scripts/build_portable.ps1` AST 通过 |
| Alembic | 全新 SQLite 从 `0001` 升到 `0008_v2_traceable_inputs (head)`；`check` 无新操作 |
| 后端包元数据 | `video2doc-backend 2.2.1` |
| 真实链接字幕 | 公开 TED URL 抓取 `zh-cn` 人工 VTT，33,238 字节，391 段 |
| 字幕 SHA-256 | `7675EBC07C5D41F4A12903FC84E9E26A37014D8914ACDB29C0F62835D8C4999E` |
| 打包首次启动 | 健康接口 `2.2.1`；首次验收因 8765 被占用自动使用 8766；最终重建包在隔离端口 8771 再验收，窗口数 1 |
| 连续重复启动 | 最终重建包再启动 4 次，退出码均为 0，耗时约 1 秒；窗口数仍为 1 且句柄 ID 未变化 |
| 安全退出 | shutdown 返回成功；窗口数降为 0；最终验收端口 8771 释放；主进程退出 |

两个跳过项仍是需要显式环境开关和真实外部模型环境的打包真实推理、文档复用
验收；警告来自 Starlette 对旧 `httpx` 测试接口的弃用提示。PyInstaller 仍报告
可选 hook 的 `tzdata`、`pysqlite2`、`MySQLdb` 未找到；本项目使用标准库
`sqlite3`，最终打包健康、迁移、窗口和退出验收均通过。

## 发行产物

- 目录：`dist/Video2Doc-Portable`
- 压缩包：`dist/Video2Doc-Portable.zip`
- EXE 大小：12,902,559 字节
- ZIP 大小：30,908,321 字节
- EXE SHA-256：
  `53488FD79DB96831FC90D7BBCF6E71777233617C5969BAB3C66F5BD472ACCEEF`
- ZIP SHA-256：
  `1402E4D56671A604C214E8DE61C52E74DD2CDFE08BB1D3B13B4457E83245A00B`
