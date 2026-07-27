# Video2Doc

Video2Doc 是一个 Windows 优先的本地 Web 应用，用于把视频或音频转换为完整、可追溯、可长期保存的文字资料。产品原则是“质量 > 成本 > 速度”，当前不会总结、提炼观点或补充视频外知识。

## 当前阶段

第四阶段已经在独立 Gateway 架构上接入真实视频 OCR 与结构化时间线融合：

```text
本地视频 / 本地音频 / 媒体网页 URL
  → 输入验证
  → 本地只读引用或 yt-dlp 下载
  → ffprobe 探测
  → FFmpeg 提取为 PCM 16kHz 单声道 WAV
  → 模型能力驱动分块（离线模型默认 5 秒重叠）
  → 独立 ASR Gateway（DashScope / FireRed / Qwen3 / Whisper）
  → 结构化边界去重与全局时间轴合并
  → 固定间隔 + 画面变化驱动抽帧
  → 感知哈希 + 内容区域视觉差异去重
  → 独立 OCR Gateway（本地 PaddleOCR 3.7 / PP-OCRv5 server）
  → OCR 原始响应、规范化文本块与稳定事件
  → ASR segment / OCR event / frame event 统一时间线
  → raw JSON / normalized JSON / TXT / SRT / 融合 JSON / 前端预览
```

耗时抽帧和 OCR 仍由 SQLite 数据库队列与 Worker 执行，不进入 API 请求线程。版本 4 不可变任务快照保存抽帧、去重、OCR 和融合参数；有效帧和逐帧 OCR 结果可在重试时按配置/内容指纹复用。OneAPI 只保留给后续文本轻度编辑，本阶段不生成最终 Markdown，也不总结或改写原文。

完整产品边界见 [产品需求](docs/PRODUCT_REQUIREMENTS.md)，设计见 [架构文档](docs/ARCHITECTURE.md)，阶段状态见 [实施计划](docs/IMPLEMENTATION_PLAN.md)。真实 OCR 部署与验收分别见 [OCR 部署报告](docs/OCR_MODEL_DEPLOYMENT_REPORT.md) 和 [OCR/Fusion 验收报告](docs/OCR_FUSION_ACCEPTANCE_REPORT.md)。

## 技术栈

- 后端：Python 3.11+、FastAPI、Pydantic、SQLAlchemy 2、Alembic、SQLite、httpx
- 前端：React 19、TypeScript 5、Vite 7
- 外部程序：FFmpeg、ffprobe、yt-dlp
- ASR：统一 Gateway `POST /v1/asr/transcriptions`；Fun-ASR 使用 DashScope WebSocket
- OCR：独立 Gateway `POST /v1/ocr/recognitions`；本地 PaddleOCR 3.7 + PP-OCRv5 server
- 图像：Pillow；媒体时间戳与候选帧由 FFmpeg `select/metadata` 提供
- 状态通信：REST + Server-Sent Events（SSE）
- 测试：pytest、受控外部进程、httpx MockTransport、Vitest

## 环境要求

- Windows 10/11 和 PowerShell 5.1+
- Python 3.11+
- Node.js 20.19+
- pnpm 10+
- FFmpeg 与 ffprobe（同一发行包）
- yt-dlp 独立可执行文件或 PATH 命令
- 云端 ASR 可选 `DASHSCOPE_API_KEY`；本地 ASR 大模型可选 WSL2 + NVIDIA GPU
- 本地 OCR 使用 Windows CPU 隔离环境，不占用 ASR 显存

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

本地 OCR Runtime 单独安装，不把 PaddlePaddle 放入后端环境：

```powershell
.\scripts\model_runtime\setup_ocr_gateway.ps1
.\scripts\model_runtime\start_ocr_gateway.ps1
.\scripts\model_runtime\test_ocr_gateway.ps1 `
  -Image D:\path\to\frame.jpg `
  -Output D:\path\to\ocr-response.json
```

默认只绑定 `127.0.0.1:8201`，停止命令为 `.\scripts\model_runtime\stop_ocr_gateway.ps1`。模型和环境默认保存在 `D:\Models\Video2Doc`，不提交到 Git。

编辑 `.env`：

```dotenv
ONEAPI_BASE_URL=https://your-oneapi.example/v1  # 仅文本处理
ONEAPI_API_KEY=replace-with-local-secret
DASHSCOPE_API_KEY=replace-with-local-secret
# DASHSCOPE_WORKSPACE_ID=optional-workspace-id
```

真实密钥只能保留在未提交的 `.env` 或进程环境变量中。前端响应、SQLite、任务快照和日志均不保存密钥。

## 数据库迁移

```powershell
Set-Location D:\Projects\Video2Doc\backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

`0005_independent_model_gateways` 新增独立 ASR/OCR Gateway；`0006_ocr_fusion_artifacts` 为任务新增帧清单、OCR 时间线和融合时间线路径。旧迁移没有改写。新任务使用快照版本 4，保存 Gateway、模型能力、抽帧/去重/OCR/融合参数、超时、重试和密钥环境变量名称，不保存密钥值。

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

1. 在“Gateway”页面创建或启用 ASR Gateway，填写协议、Base URL 与密钥环境变量名称。
2. 执行健康检查和模型发现，或手动添加模型及其能力上限。
3. 在处理方案中分别选择 ASR Gateway 模型、OCR Gateway 模型与 OneAPI 文本模型。
4. 云端密钥只通过 Gateway 记录的环境变量名称（例如 `DASHSCOPE_API_KEY`）在执行时读取。

统一 ASR 协议使用 multipart 字段 `file`、`model`、`language`、`timestamp_granularity`、`prompt` 和 `task`。Fun-ASR 对完整音频保持单一连续流；只有连接限制或恢复需要拆会话时才使用 5 秒重叠，连续会话绝不重复发送音频。

## OCR 配置

1. 启动 `local-paddleocr` Gateway，并在 Gateway 页确认健康状态与模型发现。
2. 在处理方案或新任务页选择 `PP-OCRv5_server`，启用 OCR。
3. 按内容调整固定采样间隔、场景变化阈值、最大候选帧数和融合窗口。
4. 任务详情显示候选/保留/丢弃帧、OCR 块数、OCR 事件和融合事件，并可打开工作区内关键产物。

默认使用混合采样。根据媒体时长和最大候选数推导最小选择间隔，因此 FFmpeg 会扫描完整媒体时间线而不是达到数量后提前结束。相似度特征忽略画面底部 20% 的常见字幕区，以降低字幕轻微变化造成的误保留；证据不足时保留帧并记录比较依据。

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
| `FRAME_EXTRACT` | FFmpeg 已扫描媒体时间 / 媒体总时长 |
| `FRAME_DEDUP` | 候选、保留、丢弃计数与决策校验 |
| `OCR_RECOGNIZE` | 已完成 OCR 帧数 / 保留帧数及复用数 |
| `OCR_TIMELINE` | 稳定重复页合并与增量变化事件 |
| `FUSION_BUILD` | ASR/OCR/帧统一事件构建 |
| `FUSION_VALIDATE` | 时间范围、排序、引用和产物索引校验 |

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
│   ├── chunks/
│       ├── manifest.json
│       └── chunk_0000.wav
│   └── frames/
│       ├── manifest.json
│       ├── candidates/frame_000000.jpg
│       └── normalized/frame_000000.jpg
├── 02_asr/
│   ├── provider_raw/attempt_0001/   # Provider 原始响应，按 attempt 隔离
│   ├── raw.json                     # 原始响应索引和分段
│   ├── normalized.json              # 版本化标准结果
│   ├── raw.txt
│   ├── raw.srt
│   └── preview.json
├── 03_ocr/
│   ├── provider_raw/attempt_0001/
│   ├── normalized/frames/
│   ├── raw.json
│   ├── normalized.json
│   ├── preview.json
│   └── timeline.json
├── 04_fusion/
│   ├── input.json
│   ├── timeline.json
│   ├── preview.json
│   └── intermediate/
├── 05_output/
├── logs/                            # stdout/stderr 与任务日志
├── artifacts.json
├── task_config.json
└── task_state.json
```

临时文件使用 `.part`/`.tmp` 名称，成功后原子替换；失败或取消会删除不完整媒体、音频分块和候选帧。原始 ASR/OCR 响应与规范化结果分离，后续阶段不得覆盖它们。

## 取消、超时和重试

- 等待任务取消后立即进入 `CANCELLED`。
- 运行任务写入持久化 `cancel_requested`；Provider 停止提交新分块。
- 外部命令通过统一异步执行器持续读取 stdout/stderr，并在 Windows 使用 `taskkill /T /F` 终止完整进程树，失败时再强制终止父进程。
- ASR 请求取消时取消本地 HTTP 协程；取消后返回的远端结果不会保存。
- OCR 请求使用同一关闭/取消信号；取消后不会保存晚到响应，也不会继续提交后续帧。
- 媒体命令默认超时 3600 秒，ASR 单请求默认超时 300 秒。
- ASR 对超时、网络错误、HTTP 429 和 5xx 最多重试 2 次并指数退避；认证和其他 4xx 不重试。
- `FAILED`/`INTERRUPTED` 可重试。重试增加 attempt，但继续使用创建时的原始配置快照。
- 重试会复用有效的媒体、探测、标准化音频、分块、前一 attempt ASR 响应、帧清单和逐帧 OCR；帧/OCR 复用要求媒体、配置、模型和图像内容指纹一致，不复用不完整 `.part` 文件。

可在 `.env` 调整：

```dotenv
VIDEO2DOC_MEDIA_TIMEOUT_SECONDS=3600
VIDEO2DOC_ASR_TIMEOUT_SECONDS=300
VIDEO2DOC_ASR_MAX_RETRIES=2
VIDEO2DOC_AUDIO_CHUNK_SECONDS=60
VIDEO2DOC_ASR_OVERLAP_SECONDS=5
VIDEO2DOC_PROCESS_POLL_SECONDS=0.20
```

`VIDEO2DOC_AUDIO_CHUNK_SECONDS` 只是兼容回退值，实际块长受 Gateway 模型能力上限约束。所有离线分块默认 5 秒重叠，`step_seconds = chunk_seconds - 5`；manifest 保存源时间范围、前后重叠、Provider、模型、attempt 和状态。合并层先用时间戳与规范化文本做确定性去重，不把边界重复留给后续大模型清洗；同一源窗口内足够长的相似词前缀可消除 ASR 拼写漂移造成的重复，但未匹配的边缘新词会保留。不确定冲突写入诊断，互相矛盾的词时间置 `null` 而不伪造新时间。

## API

在原有任务、队列、取消、重试、快照和 SSE API 之外，新增：

- `GET /api/v1/tasks/{id}/media`
- `GET /api/v1/tasks/{id}/transcript`
- `GET /api/v1/tasks/{id}/artifacts`
- `GET /api/v1/tasks/{id}/frames`
- `GET /api/v1/tasks/{id}/ocr`
- `GET /api/v1/tasks/{id}/timeline`
- `GET /api/v1/tasks/{id}/artifacts/{relative-path}`
- `GET/POST/PUT/DELETE /api/v1/gateways`
- `POST /api/v1/gateways/{id}/health`
- `POST /api/v1/gateways/{id}/discover`
- `GET/POST/PUT/DELETE /api/v1/gateway-models`

任务详情响应也包含媒体摘要、转写预览、帧统计、OCR 预览、融合预览、真实阶段进度和产物清单。单文件产物接口拒绝越出任务工作区。技术错误与用户可读错误分离，完整 stderr 不直接作为用户提示。

## 测试与构建

```powershell
Set-Location D:\Projects\Video2Doc
.\scripts\test_check_media_tools.ps1

Set-Location D:\Projects\Video2Doc\backend
.\.venv\Scripts\python.exe -m ruff check --no-cache app tests
.\.venv\Scripts\python.exe -m pytest -q

Set-Location D:\Projects\Video2Doc\frontend
pnpm typecheck
pnpm test
pnpm build
```

自动化测试不依赖公网或真实凭据：使用短生命周期受控进程、受控 yt-dlp/FFmpeg/OCR Provider、httpx MockTransport 和数据库 Worker 集成测试，验证成功、错误、超时、取消、时间轴、视觉去重、OCR 规范化、稳定事件、融合、脱敏、迁移、快照与重试复用。真实模型/媒体验收是独立的补充证据，不替代自动化测试。

## Windows 手工验收

仓库验收使用 `D:\Video2Doc-Test\sample.mp4`、自动提取的 `sample-asr-short.wav` 与文档记录的公开 URL：

1. 运行 `check_media_tools.ps1`。
2. 配置独立 ASR 与 OCR Gateway；OneAPI 只配置文本模型。
3. 分别创建本地音频、本地视频和 URL 任务。
4. 在任务详情核对媒体流、阶段进度、帧/OCR/融合统计和工作区产物。
5. 分别在下载、FFmpeg、ASR、抽帧和 OCR 阶段取消任务，确认任务停止、文件句柄释放且无残留进程。
6. 对失败/中断任务执行重试，核对 attempt 增加、快照不变，并只复用指纹一致的帧/OCR。
7. 运行联合启动，验证前端、健康检查、OpenAPI、SSE 和 `Ctrl+C` 清理。

ASR 部署与横向结果见 `docs/MODEL_DEPLOYMENT_REPORT.md`、`docs/ASR_MODEL_COMPARISON.md`；OCR 部署与真实全链路结果见 `docs/OCR_MODEL_DEPLOYMENT_REPORT.md`、`docs/OCR_FUSION_ACCEPTANCE_REPORT.md`。

## 已知限制

- 当前只支持单后端进程、单任务并发；不支持多实例或分布式队列。
- 当前离线分块使用固定能力上限与 5 秒重叠，尚未加入 VAD 边界微调。
- Provider 若只返回整段文本而不返回 segment 时间戳，对应标准字段保持 `null`，不会伪造时间。
- SSE 是轻量状态快照，不是可重放事件日志。
- 当前视觉去重使用内容裁剪 dHash 与灰度像素差异，不是语义视觉模型；边缘情况按保留优先。
- OCR 事件只做确定性空白清理、块级去重和相似页合并，不补写、改写或总结识别文本。
- 最终 Obsidian Markdown、文本大模型轻度编辑、RAG/问答/总结、原生文件选择器和桌面打包尚未实现。
