# Video2Doc 实施计划

更新日期：2026-07-27

## 状态标记

- `[x]` 已实现并由自动化或实际命令验证
- `[~]` 已实现，但真实外部凭据/媒体的手工验收仍待用户环境验证
- `[ ]` 后续阶段

## 阶段 0-3：第一轮工程与任务系统

- [x] FastAPI/React/SQLite/Alembic 工程骨架
- [x] 模型、处理方案和应用设置
- [x] 不可变任务配置快照
- [x] 数据库驱动 FIFO，固定并发 1 和数据库唯一约束
- [x] 等待/运行取消及完成竞态防护
- [x] 失败重试、attempt 历史和启动中断恢复
- [x] SSE 实时状态和 REST 回退
- [x] Windows 联合启动和清理脚本

## 阶段 4：真实媒体输入（第二阶段检查点 A）

- [x] 本地绝对路径与 HTTP/HTTPS URL 统一识别
- [x] 本地文件存在性、文件类型和只读引用策略
- [x] `MediaSourceResolver`、`LocalFileSource`、`YtDlpSource`
- [x] yt-dlp 元数据、下载、流式进度、stdout/stderr、超时和取消
- [x] ffprobe 原始 JSON 和标准媒体对象
- [x] 多音轨默认选择记录、无音频/损坏媒体错误
- [x] FFmpeg PCM 16kHz 单声道 WAV 标准化
- [x] FFmpeg 真实媒体时间进度、完整性检查和 `.part` 清理
- [x] 可测试的统一异步进程执行器
- [x] Windows 完整子进程树终止和后端关闭注册表
- [x] 集中的真实工作区路径服务和产物索引
- [x] `INPUT_VALIDATE` 到 `AUDIO_VALIDATE` 阶段持久化
- [x] 检查点 A 专项自动化测试
- [x] 使用本机真实 FFmpeg/ffprobe/yt-dlp 的本地视频与 Bilibili URL 验收

## 阶段 5：真实 ASR（第二阶段检查点 B）

- [x] `AsrRequest`、`AsrSegment`、`AsrResult` Provider 边界
- [x] OneAPI/OpenAI 兼容 multipart 音频转写 Provider（只保留旧快照兼容）
- [x] Base URL、模型、密钥引用、超时和重试配置
- [x] 认证、限流、超时、网络和 Provider 错误分类
- [x] HTTP 请求取消和后端关闭中断
- [x] 固定时长 PCM WAV 分块和版本化 manifest
- [x] chunk 局部时间戳映射到原媒体全局时间轴
- [x] 稳定合并顺序，缺失时间/置信度保持空值
- [x] attempt 隔离原始响应和成功 chunk 恢复复用
- [x] raw JSON、normalized JSON、TXT、SRT 和 preview 分离
- [x] `AUDIO_CHUNK` 到 `ASR_VALIDATE` 阶段持久化
- [x] 检查点 B 专项自动化测试
- [x] 受控数据库 Worker 全链路集成测试
- [x] 使用独立 DashScope、本地 FireRed、Whisper 与 Qwen Gateway 的真实转写验收

## 阶段 6：API 与必要前端

- [x] 创建真实本地路径或 URL 任务
- [x] 处理方案和任务级 ASR 模型选择
- [x] 媒体信息、标准转写和工作区产物 API
- [x] 详情页媒体流、阶段进度、分块数和转写预览
- [x] 用户错误与技术详情分离
- [x] 保留取消、重试、SSE 和 REST 回退

## 阶段 6.1：独立模型 Gateway 与重叠分块

- [x] ASR/OCR Gateway 数据库、CRUD、健康检查和模型发现
- [x] Gateway/模型分离与版本 3 不可变任务快照
- [x] OneAPI 限定为文本处理；ASR/OCR 不再依赖 OneAPI
- [x] DashScope Fun-ASR 连续 WebSocket、最终事件、词级时间戳与取消
- [x] 离线模型能力驱动块长与默认 5 秒重叠
- [x] segment/word 全局时间映射、确定性边界去重和冲突诊断
- [x] manifest 记录前后重叠、Provider、模型、attempt 与状态
- [x] 本地 FireRed/Qwen/Whisper 统一 Gateway 服务和按需单模型加载
- [x] PowerShell/WSL 安装、下载、启动、停止、健康与推理脚本
- [x] Gateway 管理与处理方案前端
- [x] 自动化覆盖重叠、去重、取消、重试复用、快照兼容和模型切换
- [x] Fun-ASR 真实短音频、完整 9 分钟连续流与取消
- [x] FireRed、Whisper、Qwen ASR + ForcedAligner 均已在本机 GPU 真实部署并完成短片段与长视频验收
- [x] Bilibili URL 下载、完整多块 ASR、合并和重试真实验收

## 阶段 6.2：视频抽帧与真实 OCR

- [x] 固定间隔、画面变化和混合抽帧策略
- [x] 从 FFmpeg 媒体 PTS 保存单调时间戳，不用循环序号伪造时间
- [x] 根据媒体时长与候选上限推导最小间隔，扫描完整视频而不提前截断
- [x] 原始帧、规范化帧、尺寸、大小、SHA-256、dHash、scene score 和决策清单
- [x] 底部字幕区裁剪后的感知哈希与视觉像素差异双层去重
- [x] 相似连续页和短暂转场过滤；不确定边缘按保留优先
- [x] 抽帧取消、超时、进程树终止和 `.part` 清理
- [x] Windows CPU PaddlePaddle 3.3.0 + PaddleOCR 3.7.0 隔离 Runtime
- [x] PP-OCRv5 server detection/recognition 真实中文英文推理
- [x] Provider、模型、帧 ID、媒体时间、完整原始响应、文本块、置信度、框、多边形和顺序统一协议
- [x] 每帧原始响应、Provider 无关规范化 JSON、预览和错误记录
- [x] 请求取消、网络/超时/429/5xx 重试和晚到结果丢弃
- [x] 帧/配置指纹与逐帧 OCR 重试复用

## 阶段 6.3：OCR 时间线与 ASR/OCR 融合

- [x] 仅做确定性空白清理、换行统一和同位置重复块删除
- [x] 综合时间、文本、空间位置和图像哈希合并稳定同页事件
- [x] 小范围文字变化保留为 `incremental_update` 并关联前一事件
- [x] 事件保存首次/最后出现时间、来源帧/图像、块、置信度、重复组和不确定性
- [x] `asr_segment`、`ocr_event`、`frame_event` 统一结构化时间线
- [x] OCR 与临近 ASR segment 时间窗关联；相似文字仍保留两种来源
- [x] 融合事件排序、ID、媒体时长、结束时间和产物引用验证
- [x] 帧/OCR/融合 API、任务详情统计与关键产物打开
- [x] 新任务页 OCR Gateway/模型与必要抽帧参数
- [x] `FRAME_EXTRACT` 到 `FUSION_VALIDATE` 阶段和真实进度
- [x] 数据库 Worker、不可变快照、失败/取消/重试语义贯穿 OCR 分支

## 阶段 7：迁移、测试与文档

- [x] `0004_core_pipeline_artifacts`、`0005_independent_model_gateways` 与 `0006_ocr_fusion_artifacts` migrations
- [x] 单元测试覆盖输入、工作区、进度解析、探测标准化、时间轴、合并和脱敏
- [x] 进程集成测试覆盖 stdout/stderr、退出码、超时和取消
- [x] HTTP mock 覆盖 ASR 成功、认证、超时和取消
- [x] 第一轮任务/队列/恢复测试回归
- [x] Ruff 后端 lint、前端 typecheck/test/build
- [x] README、架构和实施计划更新
- [x] 媒体工具环境检查脚本
- [x] 抽帧、视觉去重、OCR 规范化/取消/复用、OCR 时间线和融合单元测试
- [x] 空数据库 Alembic 升级测试与 OCR Worker/API 集成测试
- [x] 本地 PP-OCRv5 真实帧推理：40 块，全部含置信度、矩形框和多边形
- [x] `D:\Video2Doc-Test\sample.mp4` 真实数据库 Worker 全链路
- [x] 真实 OCR Gateway 中断后结构化失败、重启和逐帧 OCR 复用完成
- [x] 后端 68 tests、前端 typecheck/test/build、Alembic heads/check、脚本语法和 diff 检查

## 后续阶段（不属于本次范围）

### 文本轻度编辑与融合

- [ ] ASR/OCR/fusion 独立提示词
- [ ] OneAPI TextModelProvider
- [ ] 只执行 PRD 允许的轻度编辑
- [ ] 基于当前结构化融合结果做忠实轻度编辑，不删除来源差异

### Markdown 与 Obsidian

- [ ] 真实内容 Markdown renderer
- [ ] 来源、隐藏时间戳和原始资料索引
- [ ] 工作区与 Obsidian Inbox 双副本
- [ ] 原生打开文件/目录辅助接口

### 发布强化

- [ ] 静音边界分块评估
- [ ] SSE 可重放事件（有实际需要时）
- [ ] 原生文件/目录选择
- [ ] Windows 桌面打包、安装和升级

明确不计划实现：自动总结、核心观点、问答、RAG、向量库、知识图谱、多任务并发、多用户和云部署。
