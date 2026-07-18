# Video2Doc 实施计划

更新日期：2026-07-18

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
- [~] 使用本机真实 FFmpeg/ffprobe/yt-dlp 的三类手工媒体验收

## 阶段 5：真实 ASR（第二阶段检查点 B）

- [x] `AsrRequest`、`AsrSegment`、`AsrResult` Provider 边界
- [x] OneAPI/OpenAI 兼容 multipart 音频转写 Provider
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
- [~] 使用真实 OneAPI ASR 凭据的手工转写验收

## 阶段 6：API 与必要前端

- [x] 创建真实本地路径或 URL 任务
- [x] 处理方案和任务级 ASR 模型选择
- [x] 媒体信息、标准转写和工作区产物 API
- [x] 详情页媒体流、阶段进度、分块数和转写预览
- [x] 用户错误与技术详情分离
- [x] 保留取消、重试、SSE 和 REST 回退

## 阶段 7：迁移、测试与文档

- [x] 仅新增 `0004_core_pipeline_artifacts` migration
- [x] 单元测试覆盖输入、工作区、进度解析、探测标准化、时间轴、合并和脱敏
- [x] 进程集成测试覆盖 stdout/stderr、退出码、超时和取消
- [x] HTTP mock 覆盖 ASR 成功、认证、超时和取消
- [x] 第一轮任务/队列/恢复测试回归
- [x] Ruff 后端 lint、前端 typecheck/test/build
- [x] README、架构和实施计划更新
- [x] 媒体工具环境检查脚本

## 后续阶段（不属于本次范围）

### OCR

- [ ] 抽帧与 OCR Provider
- [ ] OCR 原始结果、时间戳和帧关联
- [ ] OCR 去重与清洗

### 文本轻度编辑与融合

- [ ] ASR/OCR/fusion 独立提示词
- [ ] OneAPI TextModelProvider
- [ ] 只执行 PRD 允许的轻度编辑
- [ ] ASR 主线与 OCR 缺失信息补充

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
