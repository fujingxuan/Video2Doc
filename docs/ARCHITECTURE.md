# Video2Doc 完整应用与 Obsidian 导出架构

## 1. 范围与不变量

完整应用在既有真实媒体、ASR、OCR 与融合架构上增加忠实文档整理、Markdown、Obsidian 导出和 Windows 单入口发行，不改变以下不变量：

- SQLite `tasks.status` 是队列事实源；最大并发数固定为 1。
- API 请求只创建/查询/控制任务，不在请求线程执行耗时 Pipeline。
- 每个任务保存不可变配置快照；重试只增加 attempt。
- Worker 启动时把遗留 `PROCESSING` 转为 `INTERRUPTED`，不猜测完成状态。
- SSE 发布数据库状态，断线时前端使用 REST 回退。
- 大型媒体、日志和转写保存在工作区；SQLite 只保存索引和小型事实。

应用包含抽帧、视觉去重、真实 OCR Provider、OCR 稳定事件、ASR/OCR/帧统一时间线、Document IR、可选忠实轻度编辑、Markdown 和原子导出。自动总结、问答、RAG、向量数据库、Embedding、语义搜索和知识提炼永久不在范围内。

## 2. 总体结构

```text
React / TypeScript
   │ REST + SSE
   ▼
FastAPI API
   ▼
TaskService ── Repository ── SQLite
   │
   ▼
SerialQueueWorker (1)
   ▼
Pipeline dispatcher
   ├── SimulatedPipelineRunner（仅显式测试兼容模式）
   └── RealPipelineRunner
         ├── MediaSourceResolver
         │     ├── LocalFileSource
         │     └── YtDlpSource
         ├── FfprobeProvider
         ├── FfmpegProvider
         ├── AudioChunker
         ├── StructuredOverlapMerger
         ├── SubtitleImporter ── SRT / VTT / ASS / SSA / TXT
         ├── DashScopeFunAsrProvider ── DashScope WebSocket
         ├── UnifiedAsrGatewayProvider
         │      ├── FireRedASR2-AED :8101
         │      ├── Qwen3-ASR + ForcedAligner :8102
         │      └── faster-whisper :8103
         ├── VideoFrameExtractor
         │      ├── FFmpeg select + media pts
         │      └── content-crop dHash + visual difference
         ├── UnifiedOcrGatewayClient
         │      └── PaddleOCR 3.7 / PP-OCRv5 server :8201
         ├── OcrTimelineBuilder
         ├── FusionTimelineBuilder
         ├── DocumentBuilder ── 类型化 Document IR
         ├── FaithfulTextEditor ── 可选 OneAPI Chat Completions
         │      └── FaithfulnessValidator / 原文回退
         ├── ObsidianMarkdownRenderer
         └── ObsidianExporter ── 原子 stage / commit / rollback

GatewayService ── gateway_configs / gateway_models
       └── ASR 与 OCR 各自拥有协议、健康检查、模型发现和密钥引用

Windows launcher
   ├── Alembic upgrade
   ├── 端口和依赖检查
   ├── FastAPI + 已构建 React 静态资源
   ├── Edge 独立应用窗口
   └── 只清理由本进程启动的子进程

OneAPI ── 仅可选的受约束文本轻度编辑
```

真实 Provider 不引用 FastAPI 路由或前端类型。Pipeline 负责阶段编排和持久化，Provider 只负责外部能力及标准化输入输出。OCR 在 ASR 验证完成后执行，但两种来源在融合和文档结果中保持独立，不把相似文字互相删除。

## 3. 输入与媒体来源

`MediaSourceResolver` 是媒体输入分类边界；`SubtitleImporter` 是可选文本来源边界：

- URL 只允许 HTTP/HTTPS，拒绝内嵌用户名和密码，交给 yt-dlp。
- 本地文件必须是存在的绝对文件路径，拒绝目录和 NUL 字符。
- 本地文件采用只读引用策略，工作区记录规范化路径和大小，不复制原文件。
- URL 下载到 `00_source/media`。
- 字幕模式仅接受视频网页 URL；Worker 使用 yt-dlp 抓取人工/自动字幕，按中文优先选择，写入 `00_source/subtitles` 并记录 SHA-256。重试会复用已验证的抓取清单和字幕文件。
- 字幕模式跳过音频提取、分块与 ASR Provider，解析结果仍进入统一的 normalized transcript、融合和 Document IR。
- 扩展名仅作界面提示；最终媒体有效性、音视频流和容器以 ffprobe 为准。

统一 `MediaAcquisitionResult` 包含 `source_type`、`original_input`、`local_media_path`、`title`、`duration`、`container`、`has_audio`、`has_video`、`metadata_path` 和 `provider`。

## 4. 异步外部进程执行器

`ExternalProcessRunner` 被 yt-dlp、ffprobe、FFmpeg 和分块器复用：

- 使用 `asyncio.create_subprocess_exec` 和参数列表，不经过 shell。
- 并发持续读取 stdout/stderr，逐行落盘并可触发进度回调。
- 以短轮询检查数据库取消标记、超时和后端关闭。
- stdout/stderr reader 与进程 wait 并行，避免管道缓冲区阻塞。
- 日志命令行会隐藏密码参数、URL 用户信息和查询参数。
- 非零退出码变成结构化错误，不静默忽略 stderr。

Windows 子进程使用新进程组启动。取消/超时/关机时，`PlatformProcessTreeTerminator` 使用 `taskkill /PID <pid> /T /F` 清理完整树，再以 `process.kill()` 作为父进程兜底。终止逻辑通过 `ProcessTreeTerminator` Protocol 可替换测试，不散落在业务 Provider 中。

全局 `ProcessRegistry` 跟踪当前子进程；FastAPI 关闭时 Worker 先调用 `shutdown_all()`，再等待 runner 结束。HTTP ASR 协程也观察同一关闭信号并取消请求。

## 5. 媒体 Provider

### yt-dlp

第一步用 `--dump-single-json --no-playlist` 获取元数据；第二步用 `--newline` 下载。下载输出模板固定在工作区，`after_move` 标记返回最终文件路径。下载进度解析 `[download] xx%`，原始元数据、stdout 和 stderr 分别保存。

采用外部可执行文件而不是 Python 内嵌包，是为了统一取消、超时、进程树、流式日志和部署检查；仓库不提交可执行文件。

### ffprobe

以 JSON 读取容器、时长、全部音频/视频流、codec、采样率、声道、分辨率和帧率。原媒体与标准化音频的原始探测 JSON 分开保存。第一版选择索引最小的第一条音频流，并把选择写入标准化对象。

### FFmpeg

明确指定所选音频流，生成 `pcm_s16le`、16 kHz、单声道 WAV。用 `.part.wav` 写入，成功且再次 ffprobe 验证后才原子改名。进度来自 `-progress pipe:1` 的 `out_time` 与媒体时长。无音频流直接报 `NO_AUDIO_STREAM`，不会生成伪造空音频。

## 6. ASR 与分块

`AudioChunker` 使用模型能力上限、确定性文件名、全局偏移和默认 5 秒重叠：

```text
chunk_0000.wav: [0, 60)
chunk_0001.wav: [55, 115)
chunk_0002.wav: [110, 170)
...
```

离线模型统一 `step_seconds = chunk_seconds - 5`，最后一块允许不足完整长度。块长由 `GatewayModel` 的 `max_chunk_seconds`、`max_alignment_seconds`、`supports_native_long_audio` 和 `supports_streaming` 决定。manifest 记录每块源起止、前后重叠、路径、Provider、模型、attempt 和状态。

`UnifiedAsrGatewayProvider` 向独立 Gateway 的 `/v1/asr/transcriptions` 提交 WAV；`DashScopeFunAsrProvider` 用单一 WebSocket 连续发送 PCM，完成后只接受最终句子。连续 Fun-ASR 会话的重叠为 0，不重复发送音频。Base URL、协议、模型、能力、超时、重试和密钥环境变量名称来自不可变任务快照。

Provider 对超时、网络错误、429 和 5xx 做有限指数退避；401/403 映射为认证错误，其他 4xx 不自动重试。取消后不会提交新分块，已取消请求的晚到结果不会持久化。

标准 `AsrSegment` 还包含结构化 words：

```text
segment_id, start_time, end_time, text, confidence,
language, provider, model, chunk_index, raw_response_reference, words
```

Provider 未返回置信度或时间戳时字段保持 `null`。局部 segment/word 时间戳仅在存在时加上 chunk 全局偏移，不伪造数据。合并先在重叠窗口用时间戳和规范化文本去重；只有词级时间戳时可裁掉已确认的重复词。同一源时间窗内足够长的词前缀即使有少量 ASR 拼写漂移，也可用规范化相似度确定消重，但最后一个匹配块之后的未匹配边缘词必须保留，避免前一块漏词。短而相似、语义不确定的内容保留并写 `boundary_conflict_retained` 诊断；若两条保留内容的词时间互相矛盾，只把冲突词时间置 `null` 并收窄 SRT segment 到下一段可靠时间，不删除文本也不编造时间。最终结果按全局时间排序，TXT、SRT 和 normalized JSON 使用同一结构化 segment 集合。

## 7. 抽帧、变化检测与视觉去重

`VideoFrameExtractor` 通过 FFmpeg `select` 与 `metadata=print` 同时输出图像和 `pts_time`/`lavfi.scene_score`。支持 `fixed_interval`、`scene_change`、`hybrid` 三种策略，时间戳始终来自媒体时间轴。

最大候选帧数不使用 `-frames:v` 提前终止解码。实现根据媒体时长和候选上限推导最小选择间隔，FFmpeg 仍扫描到视频末尾；配置、媒体大小和 mtime 组成帧清单指纹。每帧保存：

```text
frame_id, media_timestamp, extraction_reason,
original_frame_path, normalized_image_path, width, height, file_size,
sha256, perceptual_hash, scene_change_score,
kept, drop_reason, duplicate_of, similarity
```

视觉特征裁掉底部 20% 常见字幕区，再计算 64 位差分哈希与 64×36 灰度像素差异。连续相似帧需同时满足哈希和像素阈值才丢弃；短暂转场只在前后帧彼此相似时删除。单一证据不足时保留，避免 PPT 翻页、代码变化或公式逐步推导被过度合并。

候选图像先写 `.part.jpg`，成功后原子改名；取消、超时或外部进程失败会清理 partial。原媒体只读引用。

## 8. OCR Provider、原始响应与稳定事件

`UnifiedOcrGatewayClient` 使用统一 multipart 协议：

```text
POST /v1/ocr/recognitions
file, model, frame_id, media_timestamp, language
```

本地实现为 Windows CPU PaddlePaddle 3.3.0 + PaddleOCR 3.7.0，加载 `PP-OCRv5_server_det` 与 `PP-OCRv5_server_rec`。Gateway 返回 Provider、模型、帧 ID、媒体时间、完整原始 Paddle 结果、文字块、置信度、矩形框、多边形、读取顺序、语言和耗时。

Gateway 配置同时支持：

- `video2doc-ocr-v1`：当前本地进程协议；
- `paddlex-layout-parsing-v1`：PaddleOCR-VL 官方 `/layout-parsing` 服务，由轻量适配器规范化为 Video2Doc OCR 响应；
- 未来通过 Base URL 注册的同协议兼容服务。

配置记录 Provider 类型、Base URL、协议、模型、健康状态、能力、超时、重试和生命周期归属。`managed_lifecycle=false` 的 Docker/外部 Gateway 只做健康检查和调用，应用退出时不停止它。

每帧响应先保存到 `provider_raw/attempt_NNNN`，再生成 Provider 无关的 `OcrResult`。规范化只清理确定空白、统一换行、去除同位置完全重复块；置信度低于 0.5 的块标记 `uncertain`，不补写或猜测内容。

逐帧规范化文件保存图像 SHA-256、Provider、Base URL、模型、语言和超时的请求指纹。重试只有在指纹一致且原始响应仍存在时才复用。

OCR 时间线综合时间间隔、规范化文本相似度、文本块空间 IoU 和图像哈希：

- 稳定同页观察合并为一个事件并扩展 `first_seen_time/last_seen_time`。
- 小范围文字更新保留为 `incremental_update`，指向前一事件。
- 每个事件保留所有来源帧、图像和原始响应引用。
- ASR 与 OCR 文本相似不会触发跨来源删除。

## 9. ASR/OCR/帧统一时间线

`FusionTimelineBuilder` 输出 `asr_segment`、`ocr_event` 和 `frame_event`。每个事件包含稳定 ID、起止时间、来源、文本、原始产物引用、质量信息、关联帧、关联 ASR segment 和不确定性。

OCR/帧事件按可配置时间窗口关联附近 ASR segment，所有事件按媒体时间排序。验证层拒绝重复 ID、倒序时间、结束早于开始以及超出媒体时长的事件。融合结果是版本化 JSON，也是 Document Builder 的唯一时序输入；构建文档时不会回写或覆盖融合结果。

## 10. Document IR、轻度编辑与忠实性门禁

`Document`、`DocumentSection`、`DocumentBlock`、`TimestampAnchor` 和 `SourceReference` 构成版本化中间表示。块类型包括 transcript、visual_text、image、code 和 quote。每个块至少有一个来源引用，来源可定位到 ASR segment、OCR event、frame、原始响应和媒体时间。

Builder 先按时间构建带完整来源引用的原始块，并把这一版本保存为 `document.original.json`。可选 `FaithfulTextEditor` 随后把全部可编辑文本按原始顺序合并为一个 `source_text`，在单次、非分批请求中交给文本模型；模型只返回一个 `article_markdown`，形成可直接阅读的连续完整文章。无文本模型或模型回退时，也会把完整原文聚合为连续文章，而不是逐 ASR segment 渲染。

文章整理请求使用流式 Chat Completions，并用 600 秒默认墙钟上限避免兼容网关持续占用队列。GLM 4.5 及以上的默认 Thinking 会在该确定性转换任务中显式关闭；输出 token 上限按正文长度估算并限制在 4096 到 32768。流式接收器只保存最终正文以及事件数、思考字符数、完成原因和用量等诊断元数据，不保存模型思维链。JSON 对象、JSON 围栏和直接 Markdown 都可形成候选文章。

最终文章块由应用确定性继承所有原始文本块的来源引用；模型无需回显成千上万个 block/source ID。验证器对完整原文与完整候选文章检查数字、专有词形、代码标记、公式符号和文本长度。响应为空、结构无法恢复或内容明显压缩时，整个编辑响应被拒绝，Document 使用连续原文文章；原文、候选文章、原始 block/source 映射、模型、提示词版本、diff、验证指标、原始响应引用和回退原因写入文本整理产物。

## 11. Markdown 与 Obsidian 导出

Renderer 从 Document IR 生成 YAML frontmatter、媒体来源、模型/配置、连续完整正文、相对图片和可折叠追溯附录。正文不再插入逐 segment 的“讲者内容”标签或来源注释；原始分块、时间戳和 source_id 保留在 `document.original.json`、最终 Document 聚合来源以及 sidecar 中。URL 时间戳仅在追溯信息需要且来源平台支持时生成跳转；本地文件不伪造协议链接。

任务详情使用经净化的 GFM 渲染器显示 Markdown。YAML frontmatter 不作为正文显示；相对图片只有在任务产物清单中存在时才会映射到受工作区边界保护的产物 API，外部链接保持普通浏览器链接，原始 HTML 在渲染前经过白名单净化。

Exporter 先在 Inbox 内创建隐藏临时目录，复制并重命名图片，生成 Markdown、document/timeline/sources sidecar 和可选字幕/转写；全部成功后才把同一 bundle 提交到最终路径。取消或失败删除 stage，并在 overwrite 模式下恢复备份。冲突策略为 rename、fail 或 overwrite，默认 rename。最终 Markdown 不引用任务临时绝对路径。

## 12. Pipeline 阶段和进度

```text
INPUT_VALIDATE (0-3)
MEDIA_ACQUIRE (3-12)
MEDIA_PROBE (12-18)
AUDIO_EXTRACT (18-30)
AUDIO_VALIDATE (30-33)
AUDIO_CHUNK (33-38)
ASR_TRANSCRIBE (38-58)
ASR_COMBINE (58-63)
ASR_VALIDATE (63-66)
FRAME_EXTRACT (66-73)
FRAME_DEDUP (73-76)
OCR_RECOGNIZE (76-84)
OCR_TIMELINE (84-86)
FUSION_BUILD (86-88)
FUSION_VALIDATE (88-89)
DOCUMENT_BUILD (89-92)
TEXT_EDIT (92-94)
MARKDOWN_RENDER (94-97)
OBSIDIAN_EXPORT (97-99)
FINALIZE (99-100)
```

括号是总进度映射范围。每个 `task_stage_runs` 行保存 attempt、状态、阶段百分比、用户消息、技术错误、输入/输出产物和日志路径。下载、FFmpeg、分块和 ASR 使用真实比例；探测等不可精确计量步骤使用阶段状态。

更新通过短 SQLAlchemy 事务写入 `tasks` 和 `task_stage_runs`，同步 `task_state.json`，SSE 从数据库读出状态。任何 Provider 都不会直接发布 SSE。

## 13. 工作区与原始数据

`WorkspacePaths` 集中生成所有路径，Provider 不拼接任务根目录。工作区沿用 PRD 的 `00_source` 到 `05_output`，并在其内细分 media、metadata、audio、chunks、provider_raw、logs 和 intermediate。

写 JSON 使用同目录临时文件后原子替换。媒体、音频和候选帧使用 `.part` 文件。产物完成后生成 `artifacts.json`。ASR/OCR Provider 原始响应按 `attempt_0001` 隔离；`01_extract/frames/manifest.json`、`03_ocr/timeline.json` 和 `04_fusion/timeline.json` 是后续阶段的可追溯输入。

`05_output` 保存 `document.json`、`text_edit.json`、工作区 Markdown 和导出索引。SQLite 的任务行只保存媒体、转写、帧、OCR、融合、Document、Markdown、导出和产物索引路径，不保存大型响应。

## 14. 取消、失败、重试和恢复

取消竞态继续使用第一轮规则：完成更新带 `status=PROCESSING AND cancel_requested=false` 条件；取消与完成只能有一个最终赢家。

真实 runner 把错误保存为：机器码、用户消息、技术详情、阶段、可重试语义和日志路径。错误码覆盖输入、yt-dlp、ffprobe、FFmpeg、无音频、超时、取消、ASR、抽帧、OCR、融合和工作区问题。

重试复用原快照并增加 attempt。有效的获取结果、探测、标准音频、chunk manifest、ASR 响应、帧清单和逐帧 OCR 可复用；后两者要求媒体/配置/图像请求指纹一致。缺失或不完整产物重新执行，`.part` 文件永不复用。

异常后端退出仍由启动恢复统一变成 `INTERRUPTED`，不会自动重放有外部副作用的阶段。

## 15. 数据库迁移

`0005_independent_model_gateways` 在 `0004` 之后新增：

- `gateway_configs`：类型、协议、Base URL、密钥环境变量名称、健康状态与设置
- `gateway_models`：Gateway 引用、模型名、分块/对齐上限、流式与时间戳等能力
- `processing_profiles.asr_gateway_model_id`
- `processing_profiles.ocr_gateway_model_id`

`0006_ocr_fusion_artifacts` 在 `0005` 之后新增：

- `tasks.frames_manifest_path`
- `tasks.ocr_timeline_path`
- `tasks.fusion_timeline_path`

`0007_document_exports` 在 `0006` 之后新增：

- `tasks.document_path`、`tasks.markdown_path`、`tasks.export_path`
- `document_artifacts`：Document IR 索引与统计
- `text_edit_runs`：Provider、模型、提示词、接受/回退和原始响应
- `markdown_exports`：模板、最终路径、图片与 sidecar 计数

`0008_v2_traceable_inputs` 在 `0007` 之后新增：

- `task_stage_runs.model_usage`：阶段实际使用的 Gateway、协议、能力和模型显示名
- 现有方案与应用设置迁移到“成品不写入时间戳”的默认值

旧迁移没有修改。新快照版本为 6，除版本 5 内容外，还冻结 ASR/字幕文本来源、链接字幕 Provider 与语言优先级、任务级直接导出目录和无时间戳策略。抓取后的字幕文件与 SHA-256 记录在任务产物清单中。数据库与快照都只保存密钥环境变量名称。

## 16. API、前端与 Windows 启动器

原任务 API 保持兼容。真实任务选择 `asr_gateway_model_id`，处理方案分别引用 ASR/OCR Gateway 模型；模拟模式仍供显式自动化兼容测试。

新增帧、OCR、融合、文档、Markdown、系统状态、原生媒体/目录选择和受边界约束的打开/产物 API。前端新建任务页配置 ASR 或链接字幕、OCR、文本处理和直接导出目录；处理方案显示完整模型名称；详情页显示 attempt、错误、阶段进度及逐阶段模型、统计、融合、Document、Markdown 和导出位置，并提供取消、重试、打开与复制错误。SSE 和 REST 回退没有改变。

`video2doc_launcher.py` 是 Windows 单入口。它在独立 app-home 中设置数据库、数据和默认 Inbox，执行 Alembic upgrade，拒绝占用端口，启动单个 Uvicorn/FastAPI 进程并由后端托管前端；默认使用隔离 Edge profile 的应用模式窗口。命名互斥锁阻止第二个主进程，重复启动只唤醒标题精确匹配的已有窗口；主进程按真实顶层窗口句柄监控生命周期，窗口关闭后停止受管后端。退出 API 只在 launcher 注入回调时可用；应用不控制外部管理的 Docker OCR。

## 17. 安全边界

- API Key 值不进入请求响应、SQLite、快照、日志或测试 fixture；仅保存环境变量名称。
- 外部命令不用 shell，不拼接用户输入。
- URL 拒绝内嵌凭据，命令展示隐藏 URL 查询参数。
- 本地输入只读，拒绝目录、相对路径和不存在文件。
- 前端不显示完整敏感日志；任务 API 只返回产物相对路径和大小。
- 原始 ASR/OCR 响应保存前递归隐藏常见 token/key 字段。
- 单文件产物 API 对规范化绝对路径执行 `is_relative_to(workspace)` 校验，拒绝路径穿越。
- 任务级导出目录必须是已存在绝对目录；兼容 Vault/Inbox 时 Inbox 必须在 Vault 内；导出文件名经 Windows 安全清理。
- 文本模型响应必须保留 source_id，失败时回退原文；提示词和原始响应可审计。

## 18. 已知边界

- 固定时长分块尚未做静音边界检测。
- 当前只支持一个后端实例和单任务执行。
- Windows 本机使用 WinForms 选择媒体；非 Windows 环境仍使用路径输入。
- SSE 不是可重放事件日志。
- URL 下载成功仍依赖站点和本机 yt-dlp 版本。
- PP-OCRv5 server 当前在 CPU 单请求串行执行；优先质量和稳定性，不追求批量吞吐。
- 视觉去重不是语义模型，阈值边缘按保留优先。
- OCR 读取顺序使用 Provider 返回顺序；复杂图文关系只形成时间文档块，尚未重建像素级版面。
- 发行形态是便携目录/ZIP和 Edge 应用模式窗口，不是 MSI、自动更新器或内嵌 WebView。
- 本地大模型是否可运行取决于 GPU 显存、驱动、WSL 内存与具体依赖，按需单模型驻留。
