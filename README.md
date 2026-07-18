# Video2Doc

Video2Doc 是一个 Windows 优先的本地 Web 应用，用于把视频或音频转换为完整、可追溯、可长期保存的文字资料。产品原则是“质量 > 成本 > 速度”，当前不会总结、提炼观点或补充视频外知识。

## 当前阶段

第二阶段已经接入真实媒体与 ASR 核心链路：

```text
本地视频 / 本地音频 / 媒体网页 URL
  → 输入验证
  → 本地只读引用或 yt-dlp 下载
  → ffprobe 探测
  → FFmpeg 提取为 PCM 16kHz 单声道 WAV
  → 固定时长分块
  → OneAPI 的 OpenAI 兼容音频转写接口
  → 全局时间轴合并
  → raw JSON / normalized JSON / TXT / SRT / 前端预览
```

现有 SQLite 单并发队列、不可变任务快照、取消、失败、重试、启动恢复、SSE 实时更新和 REST 回退均继续使用。OCR、ASR 文本清洗、内容融合、Markdown 正文生成不在本阶段实现。

完整产品边界见 [产品需求](docs/PRODUCT_REQUIREMENTS.md)，设计见 [架构文档](docs/ARCHITECTURE.md)，阶段状态见 [实施计划](docs/IMPLEMENTATION_PLAN.md)。

## 技术栈

- 后端：Python 3.11+、FastAPI、Pydantic、SQLAlchemy 2、Alembic、SQLite、httpx
- 前端：React 19、TypeScript 5、Vite 7
- 外部程序：FFmpeg、ffprobe、yt-dlp
- ASR：OneAPI/OpenAI 兼容 `POST /v1/audio/transcriptions`
- 状态通信：REST + Server-Sent Events（SSE）
- 测试：pytest、受控外部进程、httpx MockTransport、Vitest

## 环境要求

- Windows 10/11 和 PowerShell 5.1+
- Python 3.11+
- Node.js 20.19+
- pnpm 10+
- FFmpeg 与 ffprobe（同一发行包）
- yt-dlp 独立可执行文件或 PATH 命令
- 可用的 OneAPI Base URL、API Key 和带 `ASR` 能力标签的模型

项目把 FFmpeg、ffprobe 和 yt-dlp 当作外部程序，而不提交大型二进制文件。yt-dlp 也采用外部程序方式，以便三类媒体命令统一使用异步流式日志、超时、取消和 Windows 进程树终止。

不要从不可信来源下载可执行文件。安装后运行：

```powershell
Set-Location D:\Projects\Video2Doc
.\scripts\check_media_tools.ps1
```

若程序不在 PATH，可在“应用设置”中填写 FFmpeg 和 yt-dlp 的完整路径。ffprobe 默认取 FFmpeg 同目录下的 `ffprobe.exe`。

## 安装

```powershell
Set-Location D:\Projects\Video2Doc
Copy-Item .env.example .env
.\scripts\setup.ps1
```

`setup.ps1` 安装 Python/前端依赖并检查媒体命令；媒体命令缺失时会明确警告，但仍允许先启动应用并在设置页补充路径。

编辑 `.env`：

```dotenv
ONEAPI_BASE_URL=https://your-oneapi.example/v1
ONEAPI_API_KEY=replace-with-local-secret
```

真实密钥只能保留在未提交的 `.env` 或进程环境变量中。前端响应、SQLite、任务快照和日志均不保存密钥。

## 数据库迁移

```powershell
Set-Location D:\Projects\Video2Doc\backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

第二阶段迁移 `0004_core_pipeline_artifacts` 新增任务媒体/转写/产物索引引用，以及阶段消息、技术错误、输入产物、输出产物和日志路径。大型媒体和转写仍只保存在文件系统中。

## 启动

后端：

```powershell
Set-Location D:\Projects\Video2Doc
.\scripts\start_backend.ps1
```

前端：

```powershell
Set-Location D:\Projects\Video2Doc
.\scripts\start_frontend.ps1
```

联合启动：

```powershell
.\scripts\start_dev.ps1
```

- 前端：<http://127.0.0.1:5173>
- API：<http://127.0.0.1:8000>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- OpenAPI：<http://127.0.0.1:8000/docs>

`Ctrl+C` 会停止前后端；后端停止时会终止受管外部进程树并把任务置为 `INTERRUPTED`，供用户显式重试。

## 支持的输入

- 本地视频文件
- 本地音频文件
- yt-dlp 支持的视频网页和媒体 URL

本地输入必须是存在的绝对文件路径。应用不会只根据扩展名认定媒体有效，而是最终以 ffprobe 流信息为准。当前浏览器不能安全返回任意本地绝对路径，因此前端使用路径输入；后端仍执行完整验证。

本地文件采用“只读引用原文件”策略，不复制到工作区。URL 媒体下载到任务工作区。外部命令始终使用参数列表启动，不经过 shell 拼接用户输入。

## ASR 配置

1. 在模型管理中创建模型，模型名称必须是 OneAPI 调用使用的模型 ID，并添加 `ASR` 标签。
2. 在处理方案中选择该 ASR 模型，或在新建任务页为本批任务显式选择 ASR 模型。
3. 在设置页填写 OneAPI Base URL；密钥仅通过 `ONEAPI_API_KEY` 提供。

Provider 使用 OpenAI 兼容协议：multipart 文件字段 `file`、`model`、`response_format=verbose_json` 和 segment 时间戳请求。厂商差异封装在 Provider 内，Pipeline 不依赖具体服务商。

## 真实 Pipeline 阶段与进度

| 阶段 | 进度来源 |
| --- | --- |
| `INPUT_VALIDATE` | 输入规则和文件存在性 |
| `MEDIA_ACQUIRE` | yt-dlp 实际下载百分比；本地引用直接完成 |
| `MEDIA_PROBE` | ffprobe 阶段状态（不可精确计量） |
| `AUDIO_EXTRACT` | FFmpeg `out_time` / 媒体总时长 |
| `AUDIO_VALIDATE` | 音频文件和标准化流检查 |
| `AUDIO_CHUNK` | 已完成分块数 / 总分块数 |
| `ASR_TRANSCRIBE` | 已完成 ASR 分块数 / 总分块数 |
| `ASR_COMBINE` | 全局时间轴映射和产物写入 |
| `ASR_VALIDATE` | 结果顺序、时间戳和文本检查 |

总进度由上述真实状态映射，不使用固定 sleep 伪造。无法得到比例的步骤只显示阶段级状态。

## 工作区

每个任务仍使用带 UTC 创建时间和任务 ID 的独立目录：

```text
<data>/YYYYMMDD_HHMMSS_<safe-title>_<task-id>/
├── 00_source/
│   ├── media/                       # URL 下载媒体
│   └── metadata/
│       ├── input.json
│       ├── media_acquisition.json
│       ├── ytdlp.json               # URL 原始元数据
│       ├── ffprobe.json             # 原媒体原始探测
│       ├── media_info.json          # 标准化媒体信息
│       └── audio_media_info.json
├── 01_extract/
│   ├── audio/normalized.wav
│   └── chunks/
│       ├── manifest.json
│       └── chunk_0000.wav
├── 02_asr/
│   ├── provider_raw/attempt_0001/   # Provider 原始响应，按 attempt 隔离
│   ├── raw.json                     # 原始响应索引和分段
│   ├── normalized.json              # 版本化标准结果
│   ├── raw.txt
│   ├── raw.srt
│   └── preview.json
├── 03_ocr/DISABLED.txt
├── 04_fusion/intermediate/
├── 05_output/
├── logs/                            # stdout/stderr 与任务日志
├── artifacts.json
├── task_config.json
└── task_state.json
```

临时文件使用 `.part`/`.tmp` 名称，成功后原子替换；失败或取消会删除不完整媒体/音频分块。原始 ASR 响应与标准化结果分离，后续阶段不得覆盖它们。

## 取消、超时和重试

- 等待任务取消后立即进入 `CANCELLED`。
- 运行任务写入持久化 `cancel_requested`；Provider 停止提交新分块。
- 外部命令通过统一异步执行器持续读取 stdout/stderr，并在 Windows 使用 `taskkill /T /F` 终止完整进程树，失败时再强制终止父进程。
- ASR 请求取消时取消本地 HTTP 协程；取消后返回的远端结果不会保存。
- 媒体命令默认超时 3600 秒，ASR 单请求默认超时 300 秒。
- ASR 对超时、网络错误、HTTP 429 和 5xx 最多重试 2 次并指数退避；认证和其他 4xx 不重试。
- `FAILED`/`INTERRUPTED` 可重试。重试增加 attempt，但继续使用创建时的原始配置快照。
- 重试会复用有效的媒体、探测、标准化音频、分块和前一 attempt 已成功的 ASR 分块响应；不会复用不完整 `.part` 文件。

可在 `.env` 调整：

```dotenv
VIDEO2DOC_MEDIA_TIMEOUT_SECONDS=3600
VIDEO2DOC_ASR_TIMEOUT_SECONDS=300
VIDEO2DOC_ASR_MAX_RETRIES=2
VIDEO2DOC_AUDIO_CHUNK_SECONDS=600
VIDEO2DOC_PROCESS_POLL_SECONDS=0.20
```

第一版分块策略为固定 600 秒、PCM WAV、无重叠。无重叠是有意选择，用于避免产生不可控的重复文本；每块保存原媒体全局时间偏移。

## API

在原有任务、队列、取消、重试、快照和 SSE API 之外，新增：

- `GET /api/v1/tasks/{id}/media`
- `GET /api/v1/tasks/{id}/transcript`
- `GET /api/v1/tasks/{id}/artifacts`

任务详情响应也包含媒体摘要、转写预览、真实阶段进度和产物清单。技术错误与用户可读错误分离，完整 stderr 不直接作为用户提示。

## 测试与构建

```powershell
Set-Location D:\Projects\Video2Doc\backend
.\.venv\Scripts\python.exe -m ruff check --no-cache app tests
.\.venv\Scripts\python.exe -m pytest -q

Set-Location D:\Projects\Video2Doc\frontend
pnpm typecheck
pnpm test
pnpm build
```

自动化测试不依赖公网或真实凭据：使用短生命周期受控进程、假的 yt-dlp/FFmpeg Provider、httpx MockTransport 和受控完整 Pipeline 验证成功、错误、超时、取消、时间轴、脱敏、重试快照与队列行为。

## Windows 手工验收

准备几秒钟的短 WAV、带音频的短 MP4，以及一个可公开访问且 yt-dlp 支持的短视频 URL：

1. 运行 `check_media_tools.ps1`。
2. 配置 OneAPI 和 ASR 模型。
3. 分别创建本地音频、本地视频和 URL 任务。
4. 在任务详情核对媒体流、阶段进度、分块数、转写预览和工作区产物。
5. 分别在下载、FFmpeg、ASR 阶段取消任务，确认任务停止、文件句柄释放且任务管理器中无残留进程。
6. 对失败/中断任务执行重试，核对 attempt 增加且快照不变。
7. 运行联合启动，验证前端、健康检查、OpenAPI、SSE 和 `Ctrl+C` 清理。

没有真实 OneAPI 凭据时，仍可完成媒体获取、探测、标准化之前的自动化和环境检查；真实 ASR 手工验收必须由用户提供本地凭据。

## 已知限制

- 当前只支持单后端进程、单任务并发；不支持多实例或分布式队列。
- 固定时长分块暂不做静音边界检测，也不使用重叠。
- Provider 若只返回整段文本而不返回 segment 时间戳，对应标准字段保持 `null`，不会伪造时间。
- SSE 是轻量状态快照，不是可重放事件日志。
- 还未实现 OCR、文本清洗/融合、最终 Obsidian Markdown 正文、原生文件选择器或桌面打包。
