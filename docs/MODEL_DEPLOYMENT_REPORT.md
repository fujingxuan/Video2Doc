# Video2Doc 模型部署与验收报告

更新时间：2026-07-23

状态只使用三类：**真实环境验证通过**、**仅自动化测试通过**、**未验证或失败**。模型下载成功不等同于推理成功，mock 测试不等同于真实部署。

## 1. 初始 Git 状态

**真实环境验证通过**

- 仓库：`D:\Projects\Video2Doc`
- 分支：`codex/core-pipeline-v1`
- 初始工作区：clean
- 初始 HEAD：`b1238f5 fix: stabilize media tool exit checks`
- 初始 `main..HEAD` 包含 `b1238f5`、`8ffde35`、`c7f276f`、`808f854`、`44b31b4`、`a4b2ab1`。

## 2. 本机硬件和软件环境

**真实环境验证通过**

| 项目 | 实际结果 |
| --- | --- |
| Windows | Windows 11 Home China 10.0.26200，x64 |
| CPU | AMD Ryzen 9 9955HX，16 核 / 32 线程 |
| 内存 | 31.77 GiB；审计时可用约 13.1 GiB |
| 磁盘 | C: 可用 100.9 GiB；D: 可用 342.92 GiB |
| GPU | NVIDIA GeForce RTX 5070 Ti Laptop GPU |
| 显存 | 12,227 MiB；桌面程序常驻占用约 3.5–4.1 GiB |
| NVIDIA 驱动 / CUDA | 582.05 / `nvidia-smi` CUDA 13.0 |
| WSL2 | Ubuntu，WSL version 2 |
| Docker | Docker Desktop 4.78.0、Engine 29.5.3、NVIDIA runtime 可见；VM 为 12 CPU / 15.62 GiB RAM |
| Windows Python / Conda | Python 3.13.13 / Conda 26.3.2 |
| 媒体工具 | FFmpeg/ffprobe 8.1.2；yt-dlp 2026.07.04 |

12 GiB 显存不足以可靠地让全部模型同时驻留，运行策略固定为按需启动、单模型驻留、切换时卸载和清理 CUDA cache。服务不会默认同时启动全部模型。

WSL 系统 Python 3.14.4 缺少 `python3.14-venv`，且 sudo 非免密。脚本因此使用官方 `uv 0.11.30` 管理独立 Python，不修改系统 Python。当前默认布局：

- 环境、managed Python、uv 工具和缓存：WSL ext4 的 `~/.local/share/video2doc-model-runtime`；
- 模型权重和服务日志：仓库外 `D:\Models\Video2Doc`；
- 真实 Whisper 验收复用了首次已建立在 D 盘的隔离环境；新安装默认使用 ext4，以避免 NTFS 挂载上的大量小文件性能问题。

真实 Whisper 环境为 Python 3.12.13、faster-whisper 1.2.1、CTranslate2 4.8.1、CUDA 运行库；Qwen 环境为 Python 3.12.13、qwen-asr 0.0.6、torch 2.13.0、transformers 4.57.6。FireRed 固定官方提交 `4e7d9aaf4482a47cec1724807026b9b151926eb5`，使用 Python 3.11.15、torch/torchaudio 2.10.0+cu128、transformers 5.1.0 和 `kaldi-native-fbank==1.15`。使用 Python 3.11 是因为源码实际导入但未在该提交 `pyproject.toml` 声明的 `kaldi-native-fbank==1.15` 只提供到 CPython 3.11 的 wheel。

## 3. Gateway 架构和数据库迁移

**真实环境验证通过**

- OneAPI 只用于 `text_processing`，不再承担 ASR 或 OCR。
- 新增独立 `gateway_configs` 和 `gateway_models`；Gateway 保存连接方式，模型记录能力并引用 Gateway。
- ASR 统一内部协议为 `GET /health`、`GET /v1/models`、`POST /v1/asr/transcriptions`；本地 worker 另有取消和卸载端点。
- OCR 已完成独立配置、能力、健康检查、模型发现、统一响应、API、前端和 mock 集成测试；本阶段没有伪装成真实 OCR 部署。
- Alembic 已从 `0004_core_pipeline_artifacts` 升级到 `0005_independent_model_gateways (head)`；`alembic check` 返回 `No new upgrade operations detected.`。
- 新任务快照版本为 3，记录 Gateway 类型、Base URL、协议、模型、能力、分块、时间戳、超时、重试和密钥环境变量名称；旧快照保留兼容路径。
- 数据库、任务快照、日志、异常和前端均不保存密钥值；Gateway 与模型的任意 metadata 若含 `api_key`、`access_token`、`password` 等敏感键会被 API 拒绝，遗留数据在读取和快照时也会被移除。`DASHSCOPE_API_KEY` 仅检查存在性：用户作用域存在，进程/系统作用域不存在；检查过程没有输出密钥内容。

## 4. API 与前端变化

**真实环境验证通过**

- 增加 ASR/OCR Gateway 管理页面：新增/编辑、启用状态、健康检查、模型发现、手工添加模型和连接测试。
- 模型页显示所属 Gateway、本地/云端、时间戳能力、最大分块长度与健康状态。
- 处理方案和新建任务分别选择 ASR Gateway 模型、OCR Gateway 模型和文本处理模型；文本模型继续允许使用 OneAPI。
- 后端任务快照、配置校验和目录服务均区分 ASR、OCR 与文本模型职责。
- 本机启动 API 与 Vite 后用真实浏览器检查：Gateway 页加载 5 个独立连接和 6 个 ASR 模型，显示各自分块/时间戳能力；处理方案选择 `local-whisper` 后只出现 `large-v3` 与 `large-v3-turbo`，OCR 与文本模型选择保持独立；控制台无 warning/error，结束后 8000/5173 端口均释放。
- 默认目录种子只补齐缺失 Gateway/模型；重复启动不会覆盖管理员修改过的显示名、能力或分块上限。

## 5. 端口、启动和停止

脚本语法和 mock worker 为 **仅自动化测试通过**；FireRed、Qwen 和 Whisper 的本地端口启动、健康检查、推理与停止均为 **真实环境验证通过**。

| Provider | 端口 | 启动 | 停止 | 真实状态 |
| --- | ---: | --- | --- | --- |
| FireRedASR2-AED | 8101 | `scripts\model_runtime\start_asr_gateway.ps1 -Provider firered` | `scripts\model_runtime\stop_asr_gateway.ps1 -Provider firered` | 真实环境验证通过 |
| Qwen3-ASR + Aligner | 8102 | `scripts\model_runtime\start_asr_gateway.ps1 -Provider qwen` | `scripts\model_runtime\stop_asr_gateway.ps1 -Provider qwen` | 真实环境验证通过 |
| Whisper | 8103 | `scripts\model_runtime\start_asr_gateway.ps1 -Provider whisper` | `scripts\model_runtime\stop_asr_gateway.ps1 -Provider whisper` | 真实环境验证通过 |
| mock | 8199 | `scripts\model_runtime\start_asr_gateway.ps1 -Provider mock` | `scripts\model_runtime\stop_asr_gateway.ps1 -Provider mock` | 仅自动化测试通过 |

全部服务默认只绑定 `127.0.0.1`；若未显式设置 `VIDEO2DOC_ALLOW_PUBLIC_BIND=1`，worker 拒绝非本地绑定。停止脚本等待子进程树结束并确认端口释放。

## 6. DashScope Fun-ASR

**真实环境验证通过（45 秒短音频、完整 9 分钟连续流、真实取消）**

- 模型：`fun-asr-realtime-2026-02-28`。
- 输入：`D:\Video2Doc-Test\sample-asr-short.wav`，44.9999375 秒，PCM S16LE、16 kHz、单声道。
- 墙钟 53.4 秒，得到 2 个最终句段、137 个词级时间戳，时间范围 0.08–44.98 秒。
- 原始服务事件、normalized JSON、TXT、SRT 和 preview 已保存到 `backend/data/real-acceptance/dashscope-short`。
- 单一连续 WebSocket 会话使用原始时间线，overlap 为 0，没有重复发送 5 秒音频；只有拆分独立会话时才会使用重叠与同一去重层。
- 完整 542.3485 秒音频以一个连续会话完成：19 个最终句段、1,691 个词，覆盖 0.12–542.34 秒；segment/word 时间戳倒退均为 0，完全相同句段组为 0，`chunk_count=1`。
- 完整流的原始事件、normalized JSON、TXT、SRT 与 preview 位于 `backend/data/real-acceptance/dashscope-full`。
- 真实取消在约 4.109 秒返回 `cancelled`，没有保存 normalized 输出或晚到结果。
- 超时、网络恢复、限流以及拆分多会话后的重叠重试仍为 **仅自动化测试通过**；连接建立失败与已开始流中断分别分类为 `DASHSCOPE_CONNECTION_FAILED` 和 `DASHSCOPE_STREAM_INTERRUPTED`。

实现依据阿里云当前 [Fun-ASR WebSocket API](https://help.aliyun.com/en/model-studio/fun-asr-realtime-websocket-api)、[客户端事件](https://help.aliyun.com/en/model-studio/fun-asr-client-events) 和 [服务端事件](https://help.aliyun.com/en/model-studio/fun-asr-server-events)。`DASHSCOPE_WORKSPACE_ID` 保持可选，没有从 API Key 推测 Workspace ID。

## 7. FireRedASR2-AED

**真实环境验证通过（短音频、完整 9 分钟多块、取消和重试复用）**

- 官方源码固定在提交 [`4e7d9aaf`](https://github.com/FireRedTeam/FireRedASR2S/commit/4e7d9aaf4482a47cec1724807026b9b151926eb5)，Gateway 直接调用 `FireRedAsr2.from_pretrained("aed", ...)` 和 `transcribe`；使用 GPU half、beam size 3。
- 官方 `model.pth.tar` 位于仓库外 `D:\Models\Video2Doc\firered\FireRedASR2-AED`，实际大小 4,731,558,506 bytes；检查时没有 `.incomplete` 文件。下载中出现过一次 `ChunkedEncodingError`，受控服务随后从约 2.925 GB 继续下载并完整结束，没有删除已下载内容。依赖安装服务最终 `Result=success`、零重启。
- 45 秒样本真实推理：墙钟 41.186 秒、首次加载 34.036 秒、1 段、223 个词级时间戳，覆盖 0.0–45.0 秒且不倒退；模型返回 segment confidence 0.98，未返回语言，因此语言保持 `null`。显存基线 1,958 MiB、峰值 5,642 MiB、增量 3,684 MiB。
- 完整 542.3485 秒视频按 55 秒块长、5 秒重叠形成 11 块；实际模型请求阶段 74.004 秒。最终合并 11 段、2,762 个词，10 个长重叠窗口按源时间与规范化文本相似度消重，另有 1 组按精确词时间戳消重。
- 合并共 16 条诊断；7 个无法确定的边缘词保留文本并把冲突时间置 `null`。最终 segment 覆盖 0、非空词时间戳倒退 0，TXT/SRT 没有边界重复块。retry 复用 11/11 原始成功块，新 Gateway 请求为 0。
- 人工检查约 50、100、500 秒：100 秒处 `eagle browser / ego brother` 的同源重复已确定消除；500 秒附近的边缘新词“百度地图”完整保留；50 秒处模型对跨边界词有不同拆分，未丢整段且记录冲突。取消测试只生成前 2 个 chunk，未继续生成后续 chunk。
- 服务真实启动后 health 返回 CUDA/已加载模型状态；停止脚本完成后 8101 端口释放。能力上限固定为 55 秒（硬上限不超过 60 秒），来源与部署入口见 [FireRedTeam/FireRedASR2S 官方仓库](https://github.com/FireRedTeam/FireRedASR2S)。

## 8. Qwen3-ASR 与 ForcedAligner

**真实环境验证通过（短音频、完整 9 分钟多块、重试复用）**

- 已安装官方 qwen-asr 0.0.6 Transformers 后端；batch size 为 1，Forced Aligner 单块上限不超过 300 秒，当前 ASR 能力块长采用 180 秒。
- 已修正官方返回结构适配：单条结果的 `time_stamps` 本身就是 ForcedAlignItem 列表，不能额外取一层索引；自动化测试覆盖全部对齐条目。
- `Qwen/Qwen3-ASR-1.7B` 与 `Qwen/Qwen3-ForcedAligner-0.6B` 权重均完整；首次 Xet 传输长时间无进展后精确终止该下载进程，以 `HF_HUB_DISABLE_XET=1` 断点续传成功，未删除已有文件。
- 45 秒样本真实推理：墙钟 84.732 秒、加载 70.294 秒、1 段、227 个对齐词项、语言 `Chinese`；词时间覆盖 0.0–44.96 秒、倒退 0。显存基线 3,168 MiB、峰值 9,635 MiB、增量 6,467 MiB。
- 完整 542.3485 秒视频使用 180 秒块长、5 秒重叠，共 4 块；首次实际模型运行总墙钟约 135.2 秒。复用原始响应重放最终合并后得到 4 段、2,751 个词项、4 条边界诊断，确定删除 3 组长重叠前缀。
- 仅 1 个无法确定的边缘词保留文本并把冲突时间戳置为 `null`；最终 segment 边界覆盖 0、非空词时间戳倒退 0。TXT、SRT、normalized JSON 均生成，retry 复用 4/4 原始成功块且新请求为 0。
- 人工检查约 179.64、355.52、530.04 秒三个边界：同源长重叠前缀已消除；`Pop/Puppeteer` 和 `Eagle/Browser` 的跨边界词保留为一次连续表达，没有时间戳覆盖、明显重复或整段丢失。
- 官方用法与能力来源：[QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)。

## 9. Whisper large-v3 与 large-v3-turbo

**真实环境验证通过**

两套权重均位于仓库外 `D:\Models\Video2Doc\whisper`，worker 使用 faster-whisper，支持自动/指定语言、句/词时间戳、VAD、beam size 5、temperature 0、`condition_on_previous_text`，并按模型名切换。真实短音频结果：

| 模型 | 墙钟 | 加载 | 观察到的峰值显存 | 结果 |
| --- | ---: | ---: | ---: | --- |
| large-v3 | 34.983 秒 | 20.501 秒 | 8,415 MiB（相对基线 +4,351 MiB） | 6 段、212 词、语言 zh |
| large-v3-turbo | 10.795 秒 | 8.942 秒 | 8,018 MiB（切换测试基线已含前一模型占用，不能作为独立增量） | 5 段、217 词、语言 zh |

真实模型切换成功，health 显示当前加载模型；停止后 8103 端口释放。实现与依赖选择参考 [faster-whisper 官方仓库](https://github.com/SYSTRAN/faster-whisper)。

## 10. 动态分块、全局时间轴与去重

**真实环境验证通过**

- 非流式 ASR 的默认步长为 `chunk_seconds - 5`，最后一块允许不足完整长度。
- manifest 记录 `chunk_index`、源起止、前后重叠、音频路径、provider、model、attempt 和 status。
- Provider 局部 segment/word 时间戳先映射到源视频全局轴，再在相邻重叠窗口中处理。
- 完全相同或仅标点/大小写/空白不同的内容按时间戳和规范化文本确定性删除；有可靠词时间戳时还会比较同一源重叠窗口内的长前缀，消除轻微 ASR 拼写漂移造成的重复。
- 长前缀相似消重要求同一时间窗口、至少 12 个规范化字符和足够相似度；最后一个匹配块之后的未匹配边缘词始终保留，避免前一块漏词造成内容丢失。
- 语义可能不同或证据不足时保留内容并写 `boundary_conflict_retained`，不冒险模糊删除。
- 合并输出先验证时间戳非递减，TXT、SRT 与 normalized JSON 由同一结构化结果生成。

## 11. 本地视频完整验收

**真实环境验证通过（Whisper large-v3-turbo）**

输入 `D:\Video2Doc-Test\sample.mp4`，542.3485 秒：

- 30 秒分块、5 秒重叠、22 块；最后一块 525.0–542.3485 秒，`overlap_before=5`、`overlap_after=0`。
- 最终重放合并为 192 段、2,593 个词；56 条诊断包含 8 个重复 segment、18 组精确词重叠和 16 组长相似前缀消重；8 个不确定词保留文本但时间为 `null`。segment 边界覆盖与非空词时间戳倒退均为 0。
- ASR 请求阶段约 30.461 秒；生成 TXT、SRT、normalized JSON 和每块原始响应。
- 重试 attempt 2 复用 22/22 成功块，新增 Gateway 请求 0，重复写入段 0。
- 取消测试在生成 2 个 chunk 后停止，没有生成后续 chunk 或 manifest。

人工检查 25、50、275 秒三个边界：上下文连续，没有明显重复、时间戳倒退或整段丢失；词形有差异时，结构化层只在同一源时间窗和长文本相似度共同成立时删除重复，未匹配边缘词仍保留。

## 12. Bilibili URL 真实验收

**真实环境验证通过（下载 + Whisper large-v3-turbo）**

- URL：`https://www.bilibili.com/video/BV1pYNm69EPm`。
- 生产 `MediaSourceResolver` 通过 yt-dlp 下载公开可用格式，文件 86,437,320 bytes；ffprobe 确认为 1,891.358186 秒、含音视频流。
- 完整音频使用 30 秒分块、5 秒重叠，共 76 块；最后一块 1875.0–1891.3581875 秒，后重叠为 0。
- 最终重放合并为 846 段、8,932 词、286 条诊断：50 个 segment、101 组精确 word 和 34 组长相似前缀被确定消重；66 个冲突词保留文本但时间为 `null`。segment 边界覆盖与非空词时间戳倒退均为 0。
- ASR 请求阶段约 125.811 秒；TXT、SRT、normalized JSON 与全部原始响应均已生成。
- 重试复用 76/76 块，新增 Gateway 请求 0，重复写入段 0。

人工检查 25、50、500 秒边界：语句连续且无明显边界重复；500 秒附近的未匹配边缘内容得到保留，没有时间戳倒退或整段丢失。TXT/SRT 中相同文本的重复经全局时间核对均位于相距较远的真实重复话语，不是相邻 chunk 复制。

## 13. 自动化测试和构建

**真实环境验证通过**

| 验收项 | 结果 |
| --- | --- |
| Ruff | 全部通过 |
| Pytest | 59 passed，1 条 Starlette TestClient 上游弃用警告 |
| Alembic current/check | `0005_independent_model_gateways (head)`；无新操作 |
| `pnpm typecheck` | 通过 |
| `pnpm test` | 1 passed |
| `pnpm build` | 42 modules transformed，成功 |
| PowerShell AST parse | `scripts/model_runtime/*.ps1` 全部通过 |
| 媒体脚本回归 | FFmpeg、ffprobe、yt-dlp 检查与退出码测试通过 |
| `git diff --check` | 通过；仅 Git 的 CRLF 转换提示 |

自动化覆盖 Gateway 迁移/密钥脱敏、种子配置持久性、动态分块、完整/标点差异去重、相似语义保留、segment/word/无时间戳、时间戳非递减、TXT/SRT/JSON 去重、取消、重试复用、DashScope/Qwen/Whisper 标准化、OCR mock、健康检查、模型切换与旧快照兼容。云服务和大模型不作为普通 CI 硬依赖。

## 14. 未完成或硬件受限项目

**未验证或失败（仅下列缺少合适素材或未主动制造的场景）**

- Qwen3-ASR + ForcedAligner：中文及中英混合真实推理通过；缺少独立英语口语和四川口音标注样本，因此这两项不作准确率结论。
- Fun-ASR：45 秒和完整 9 分钟连续流、真实中途取消均通过；网络恢复和拆分会话重叠尚未真实制造故障验证。
- 四川口音/方言：现有片段不足以给出可靠结论；不伪造 WER/CER。
- 12 GiB 总显存且桌面占用较高；Qwen ASR + Aligner 已真实同载，但仍采用单模型驻留，避免与其他大模型同时加载造成 OOM。

## 15. 提交、push、PR 和当前状态

**最终 Git 状态将在最终回归和文档提交后更新**

- 当前分支：`codex/core-pipeline-v1`。
- 已创建本地提交：`67f354f feat: add independent capability-driven model gateways`、`711b893 feat: add local ASR model runtime services`、`e3a4e92 test: cover gateway integrations and ASR overlap`。
- 不自动合并 `main`；push 和 PR 仅在最终回归全部通过后执行。
