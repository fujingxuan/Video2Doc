# Video2Doc 第一轮架构

## 1. 范围与目标

当前版本是 Windows 优先的本地 Web 应用骨架。它验证模型/方案/设置管理、不可变任务配置快照、SQLite 持久化单并发队列、模拟阶段、取消、失败、重试、重启恢复和前端实时状态。所有运行结果均明确标注为“模拟 Pipeline”。

本轮不读取媒体内容，不调用 yt-dlp、FFmpeg、ASR、OCR、OneAPI、Ollama 或远程模型，不解析网页，也不生成或伪造媒体正文。未来真实 Pipeline 必须替换适配器/runner，不能改变既有队列与快照语义。

## 2. 总体架构

```text
React + TypeScript + Vite
        │ HTTP JSON + SSE
        ▼
FastAPI API 层
        ▼
应用 Service 层
        ├── Repository / SQLAlchemy ── SQLite
        ├── SerialQueueWorker (并发固定为 1)
        └── SimulatedPipelineRunner ── 任务工作区 / 模拟 Markdown
```

前后端可独立启动。FastAPI 负责进程生命周期，后台 Worker 不依赖浏览器是否打开；React 首次通过 REST 读取持久化状态，随后通过 SSE `/api/v1/events` 接收变化。SSE 仅推送状态发生变化时的完整轻量视图，并发送低频 keep-alive；浏览器自动重连期间，前端每两秒通过 REST 回退刷新，并在 SSE 或 REST 恢复时清除旧错误态，因此断线不会让页面永久停止更新。

## 3. 后端模块

- `app/api`：HTTP 路由、请求/响应协议和 SSE，不承载业务决策。
- `app/schemas`：Pydantic 输入输出模型与固定能力枚举。
- `app/services`：模型/处理方案校验、设置、批量任务、快照、取消与重试。
- `app/repositories`：SQLAlchemy 查询封装。
- `app/models`：数据库实体与关系。
- `app/workers`：唯一的串行调度 Worker。
- `app/pipeline`：可替换的 Pipeline 边界；当前只有模拟 runner。
- `app/storage`：Windows 安全文件名、任务工作区、状态 JSON、日志和模拟输出。
- `app/core`：环境配置、统一业务异常和日志。
- `app/db`：引擎、Session 与 SQLite 外键/等待超时配置。

未处理异常不会被静默吞掉：业务异常映射为统一 JSON；数据库异常记录堆栈并返回脱敏错误；runner 的未知异常会落为 `FAILED` 后继续记录完整应用日志。

## 4. 前端模块

- `api/client.ts`：唯一 REST 客户端和错误解析。
- `types.ts`：与 API 对齐的数据类型。
- `components`：布局、状态徽章与加载/空/错误态。
- `pages`：总览、新建任务、队列、详情、模型、处理方案、设置。
- `utils`：状态标签和时间格式化，并有 Vitest 测试。

页面不是静态占位：模型、方案、设置、创建任务、取消、重试和详情均调用真实后端 API。浏览器无法提供任意本地绝对路径，因此第一轮采用路径输入；未来可由本地后台提供原生目录选择辅助接口。

## 5. 数据库设计

SQLite 保存小型结构化事实和任务索引，不存媒体 BLOB 或大段转写：

| 表 | 职责 |
| --- | --- |
| `model_configs` | 用户手工模型 ID、备注、启停状态 |
| `model_capabilities` | 模型的固定能力标签；同模型同标签唯一 |
| `processing_profiles` | 可复用的完整工作流配置和三个模型外键 |
| `app_settings` | 单例设置、上次方案和上次任务配置 |
| `tasks` | 队列状态、阶段、进度、错误、路径、执行时间和重试次数 |
| `task_config_snapshots` | 与任务一对一的完整 JSON 快照 |
| `task_stage_runs` | 每次 attempt 的阶段状态、进度、时间和错误 |

所有表由 Alembic 创建。外键在每个 SQLite 连接上启用；模型被方案引用时禁止删除，任务删除会级联清理快照和阶段记录。时间统一按 UTC 写入。JSON 用于有版本的配置快照，不用于媒体内容。

## 6. 固定模型能力与处理方案

能力只允许 `ASR`、`OCR`、`TEXT_PROCESSING`、`VISION`。创建/修改方案时，Service 验证每个所选模型已启用且拥有对应能力；OCR 开启时必须选择 OCR 模型。前端下拉框执行同样的能力过滤，但后端仍是最终校验边界。

## 7. 配置快照

创建任务时，Service 将处理方案解析为普通值，记录所选模型名称、全部处理开关、输出/资料路径、输入信息与显式覆盖值，然后深复制到 `task_config_snapshots.snapshot_json`，并写入工作区 `task_config.json`。快照不保存 API Key；密钥只允许通过 `ONEAPI_API_KEY` 环境变量提供。

后续修改模型、方案或全局设置不会更新既有快照。重试沿用原快照并增加 `attempt`，保证历史可追溯。`app_settings.last_task_config` 保存上一次实际配置，供下次创建恢复，但它与每个任务的历史快照互不引用。

## 8. 持久化单并发队列

队列事实源是 `tasks.status`，内存中只保留一个唤醒事件和 Worker 协程。Worker 每次领取最早的 `QUEUED` 任务；领取前若数据库已有 `PROCESSING` 任务则不再领取，SQLite 部分唯一索引 `uq_tasks_single_processing` 还会从数据库层拒绝第二条 `PROCESSING` 记录。因此同一应用进程中并发固定为 1，并且数据库状态始终满足单执行约束。第一轮只支持一个后端实例；多实例部署不在产品范围，若未来支持仍需任务领取租约/锁以避免两个进程重复领取同一行。

处理完成、失败或取消后，Worker 自动领取下一项。每个模拟阶段在数据库创建 `task_stage_runs`，分段更新总进度与阶段进度，并同步 `task_state.json` 和任务日志。OCR 关闭时不执行 `OCR_PROCESSING`，工作区仍创建 `03_ocr/DISABLED.txt`，以保持目录结构一致且明确说明未启用。

### 取消竞态

- 等待任务立即原子地变为 `CANCELLED`。
- 运行任务设置持久化 `cancel_requested`；runner 在每个进度片段检查。
- 最终完成使用带 `status=PROCESSING AND cancel_requested=false` 条件的更新，与取消提交竞争；只有一个更新能成功，避免“已完成但仍请求取消”的矛盾状态。

### 失败与重试

真实异常或测试用的显式失败模式把任务设为 `FAILED`，记录错误代码、用户可读消息和阶段错误。只有 `FAILED`/`INTERRUPTED` 可重试；重试清理当前运行字段、`attempt + 1`、重新入队，但保留旧阶段记录与原始快照。

### 重启恢复

FastAPI 启动 Worker 前，将遗留的 `PROCESSING` 任务和正在运行的阶段标记为 `INTERRUPTED`，错误代码为 `backend_restart`。它不会猜测任务已完成，也不会自动重放有副作用的阶段；用户可显式重试。`QUEUED`、`COMPLETED`、`FAILED`、`CANCELLED` 保持不变。

## 9. 模拟 Pipeline 与工作区

模拟阶段为：

```text
PREPARING → EXTRACTING → ASR_PROCESSING
→ [OCR_PROCESSING，仅 OCR 开启]
→ TEXT_PROCESSING → GENERATING_OUTPUT → COMPLETED
```

它们仅休眠并持久化进度。每个任务创建带 UTC 时间戳、安全标题和任务 ID 的目录，包含 `00_source` 至 `05_output`、日志、`task_config.json` 和 `task_state.json`。完成时生成一份明确声明“未处理媒体、未调用模型”的模拟 Markdown，工作区保留副本，并以不覆盖同名文件的方式复制到 Inbox。资料目录不会复制到 Inbox。

## 10. API 组织

所有业务 API 位于 `/api/v1`：

- `GET /health`
- `/models` CRUD 与 `GET /models/capabilities`
- `/profiles` CRUD
- `GET/PUT /settings`
- `POST/GET /tasks`、`GET /tasks/{id}`、`POST .../cancel`、`POST .../retry`
- `GET /tasks/{id}/snapshot`
- `GET /queue`
- `GET /events`（SSE）

FastAPI 自动生成 `/docs` OpenAPI 页面。业务错误格式为 `{"error":{"code":"...","message":"..."}}`。

## 11. 技术选择

- FastAPI/Pydantic：类型清晰、自动 OpenAPI、适合本地异步生命周期。
- SQLAlchemy 2 + Alembic：模型和迁移职责分离，未来可演进 schema。
- SQLite：单用户、单机、串行队列无需额外服务，部署最简单。
- asyncio Worker + 同步短事务：任务调度简单可测，避免引入 Celery/Redis。
- React/TypeScript/Vite：小型可维护 SPA；本轮不引入额外状态框架。
- SSE：单向状态变化足够，复杂度低于 WebSocket，避免高频轮询。
- JSON 快照 + 明确 schema version：保留完整历史，并为未来升级提供版本边界。

## 12. 已知边界与后续真实 Pipeline

当前 Worker 只支持单后端进程；SQLite 不作为媒体存储；SSE 目前推送轻量任务列表而非可重放事件日志；本轮没有操作系统文件夹选择和打开文件接口。模拟阶段名称与未来真实能力概念对应，但没有伪造任何识别结果。

下一阶段接入真实媒体时，应先定义 `MediaDownloader`、`AudioExtractor`、`ASRProvider`、`OCRProvider`、`TextModelProvider`、`OutputWriter` 和 Windows 子进程管理器。Provider 的输入输出、阶段产物和取消契约必须独立于 API/Worker；真实能力不得直接写进路由或替换快照规则。
