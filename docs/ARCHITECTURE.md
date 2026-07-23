# Video2Doc 独立模型 Gateway 架构

## 1. 范围与不变量

第二阶段在第一轮骨架上加入真实媒体输入和真实 ASR，不改变以下不变量：

- SQLite `tasks.status` 是队列事实源；最大并发数固定为 1。
- API 请求只创建/查询/控制任务，不在请求线程执行耗时 Pipeline。
- 每个任务保存不可变配置快照；重试只增加 attempt。
- Worker 启动时把遗留 `PROCESSING` 转为 `INTERRUPTED`，不猜测完成状态。
- SSE 发布数据库状态，断线时前端使用 REST 回退。
- 大型媒体、日志和转写保存在工作区；SQLite 只保存索引和小型事实。

本阶段包含 OCR Gateway 架构但不包含真实 OCR 模型；文本清洗、融合、Markdown 正文生成、总结和知识提炼仍不在范围内。

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
         ├── AudioChunker + StructuredOverlapMerger
         ├── DashScopeFunAsrProvider ── DashScope WebSocket
         └── UnifiedAsrGatewayProvider
                ├── FireRedASR2-AED :8101
                ├── Qwen3-ASR + ForcedAligner :8102
                └── faster-whisper :8103

GatewayService ── gateway_configs / gateway_models
       └── ASR 与 OCR 各自拥有协议、健康检查、模型发现和密钥引用

OneAPI ── 仅后续文本处理模型
```

真实 Provider 不引用 FastAPI 路由或前端类型。Pipeline 负责阶段编排和持久化，Provider 只负责外部能力及标准化输入输出。

## 3. 输入与媒体来源

`MediaSourceResolver` 是唯一输入分类边界：

- URL 只允许 HTTP/HTTPS，拒绝内嵌用户名和密码，交给 yt-dlp。
- 本地文件必须是存在的绝对文件路径，拒绝目录和 NUL 字符。
- 本地文件采用只读引用策略，工作区记录规范化路径和大小，不复制原文件。
- URL 下载到 `00_source/media`。
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

## 7. Pipeline 阶段和进度

```text
INPUT_VALIDATE (0-5)
MEDIA_ACQUIRE (5-20)
MEDIA_PROBE (20-28)
AUDIO_EXTRACT (28-43)
AUDIO_VALIDATE (43-47)
AUDIO_CHUNK (47-55)
ASR_TRANSCRIBE (55-88)
ASR_COMBINE (88-96)
ASR_VALIDATE (96-100)
```

括号是总进度映射范围。每个 `task_stage_runs` 行保存 attempt、状态、阶段百分比、用户消息、技术错误、输入/输出产物和日志路径。下载、FFmpeg、分块和 ASR 使用真实比例；探测等不可精确计量步骤使用阶段状态。

更新通过短 SQLAlchemy 事务写入 `tasks` 和 `task_stage_runs`，同步 `task_state.json`，SSE 从数据库读出状态。任何 Provider 都不会直接发布 SSE。

## 8. 工作区与原始数据

`WorkspacePaths` 集中生成所有路径，Provider 不拼接任务根目录。工作区沿用 PRD 的 `00_source` 到 `05_output`，并在其内细分 media、metadata、audio、chunks、provider_raw、logs 和 intermediate。

写 JSON 使用同目录临时文件后原子替换。媒体和音频使用 `.part` 文件。产物完成后生成 `artifacts.json`。ASR Provider 原始响应按 `attempt_0001` 隔离；`raw.json` 是原始响应索引，`normalized.json` 是版本化标准数据，另存 `raw.txt`、`raw.srt` 和 `preview.json`。

SQLite 的任务行只保存 `media_info_path`、`transcript_path` 和 `artifacts_index_path`，不保存大型响应。

## 9. 取消、失败、重试和恢复

取消竞态继续使用第一轮规则：完成更新带 `status=PROCESSING AND cancel_requested=false` 条件；取消与完成只能有一个最终赢家。

真实 runner 把错误保存为：机器码、用户消息、技术详情、阶段、可重试语义和日志路径。错误码覆盖输入、yt-dlp、ffprobe、FFmpeg、无音频、超时、取消、ASR 配置/认证/限流/超时/Provider 和工作区问题。

重试复用原快照并增加 attempt。有效的获取结果、探测、标准音频和 chunk manifest 可复用；ASR 会搜索前一 attempt 已成功的同索引原始响应，避免重复收费。缺失或不完整产物重新执行，`.part` 文件永不复用。

异常后端退出仍由启动恢复统一变成 `INTERRUPTED`，不会自动重放有外部副作用的阶段。

## 10. 数据库迁移

`0005_independent_model_gateways` 在 `0004` 之后新增：

- `gateway_configs`：类型、协议、Base URL、密钥环境变量名称、健康状态与设置
- `gateway_models`：Gateway 引用、模型名、分块/对齐上限、流式与时间戳等能力
- `processing_profiles.asr_gateway_model_id`
- `processing_profiles.ocr_gateway_model_id`

旧任务快照仍可由兼容 Provider 执行；新快照版本为 3，数据库与快照都只保存密钥环境变量名称。

## 11. API 与前端

原任务 API 保持兼容。真实任务选择 `asr_gateway_model_id`，处理方案分别引用 ASR/OCR Gateway 模型；模拟模式仍供显式自动化兼容测试。

新增 Gateway CRUD、健康检查、模型发现和 Gateway 模型 API。前端 Gateway 页分别管理 ASR/OCR 连接；模型页只保留 OneAPI 文本/视觉模型；处理方案和新任务页选择独立 Gateway 模型。SSE 和 REST 回退没有改变。

## 12. 安全边界

- API Key 值不进入请求响应、SQLite、快照、日志或测试 fixture；仅保存环境变量名称。
- 外部命令不用 shell，不拼接用户输入。
- URL 拒绝内嵌凭据，命令展示隐藏 URL 查询参数。
- 本地输入只读，拒绝目录、相对路径和不存在文件。
- 前端不显示完整敏感日志；任务 API 只返回产物相对路径和大小。
- 原始 ASR 响应保存前递归隐藏常见 token/key 字段。

## 13. 已知边界

- 固定时长分块尚未做静音边界检测。
- 当前只支持一个后端实例和单任务执行。
- 浏览器没有原生绝对路径选择器，当前使用路径输入。
- SSE 不是可重放事件日志。
- URL 下载成功仍依赖站点和本机 yt-dlp 版本。
- OCR Gateway 当前只有完整抽象与 mock，没有宣称真实 OCR 部署。
- 本地大模型是否可运行取决于 GPU 显存、驱动、WSL 内存与具体依赖，按需单模型驻留。
