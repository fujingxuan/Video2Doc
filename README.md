# Video2Doc

Video2Doc 是一个 Windows 优先的本地 Web 应用，长期目标是把视频或音频转换为完整、结构化、可追溯、可长期保留的 Obsidian Markdown 学习材料。产品原则是完整保留信息，AI 不替用户总结、提炼或生成知识观点。

## 当前阶段

当前仓库完成了第一轮工程初始化：模型、处理方案、应用设置、任务配置快照、SQLite 持久化单并发队列、模拟进度、取消、失败、重试、重启恢复、SSE 实时状态和基础操作界面。

> 当前 Pipeline 是明确标注的模拟实现。它不会读取或下载媒体，不会调用 yt-dlp、FFmpeg、ASR、OCR、OneAPI、Ollama 或任何远程模型，也不会伪造媒体正文。

完整产品要求见 [docs/PRODUCT_REQUIREMENTS.md](docs/PRODUCT_REQUIREMENTS.md)，当前设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，阶段状态见 [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)。

## 技术栈

- 后端：Python 3.11+、FastAPI、Pydantic、SQLAlchemy 2、Alembic、SQLite
- 前端：React 19、TypeScript 5、Vite 7
- 状态通信：REST + Server-Sent Events（SSE）
- 测试：pytest、FastAPI TestClient、Vitest
- 包管理：Python `venv`/pip、pnpm

## 目录结构

```text
Video2Doc/
├── backend/
│   ├── app/
│   │   ├── api/             # HTTP 与 SSE
│   │   ├── core/            # 配置、日志、统一异常
│   │   ├── db/              # SQLAlchemy 引擎与 Session
│   │   ├── models/          # 持久化实体
│   │   ├── pipeline/        # 可替换 runner；当前仅模拟
│   │   ├── repositories/    # 数据访问
│   │   ├── services/        # 应用业务规则
│   │   ├── storage/         # 工作区与模拟输出
│   │   └── workers/         # 固定并发 1 的 Worker
│   ├── migrations/          # Alembic 迁移
│   └── tests/               # API/队列/恢复测试
├── frontend/
│   ├── src/api/             # API 客户端
│   ├── src/components/      # 通用界面组件
│   ├── src/pages/           # 七个功能页面
│   └── src/utils/           # 展示工具与测试
├── docs/
├── scripts/
├── .env.example
└── .gitignore
```

运行时数据库、日志、任务目录、虚拟环境、`node_modules` 和前端构建产物均被 Git 忽略。

## 环境要求

- Windows 10/11 与 PowerShell 5.1+（PowerShell 7 也可）
- Python 3.11 或更新版本
- Node.js 20.19+（Vite 7 要求）
- pnpm 10+；推荐使用仓库声明的 pnpm 11
- 首次安装依赖时需要网络

本轮不需要 FFmpeg、yt-dlp、OCR 命令或 OneAPI 密钥。

## 一键安装

在仓库根目录打开 PowerShell：

```powershell
Set-Location D:\Projects\Video2Doc
Copy-Item .env.example .env
.\scripts\setup.ps1
```

若系统阻止本地脚本，可只为当前 PowerShell 进程放宽策略：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

不要把含真实密钥的 `.env` 提交到 Git。

## 后端安装、迁移与启动

手动方式（从仓库根目录开始）：

```powershell
Set-Location D:\Projects\Video2Doc
py -3 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
Set-Location .\backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

或使用脚本（会先执行迁移）：

```powershell
Set-Location D:\Projects\Video2Doc
.\scripts\start_backend.ps1
```

服务地址：

- API：<http://127.0.0.1:8000>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- OpenAPI：<http://127.0.0.1:8000/docs>

### 数据库迁移

```powershell
Set-Location D:\Projects\Video2Doc\backend
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
```

创建新迁移时，应先修改 SQLAlchemy 模型，再审阅自动生成的迁移：

```powershell
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe schema change"
```

不要使用 `Base.metadata.create_all()` 替代生产迁移；它只在隔离测试 fixture 中用于快速建库。

## 前端安装与启动

```powershell
Set-Location D:\Projects\Video2Doc\frontend
pnpm install
pnpm dev --host 127.0.0.1
```

打开 <http://127.0.0.1:5173>。默认 API 地址为 `http://127.0.0.1:8000/api/v1`；需要覆盖时在仓库根目录 `.env` 中设置 `VITE_API_BASE_URL`，或启动前设置同名环境变量。

也可以从根目录运行：

```powershell
.\scripts\start_frontend.ps1
```

## 同时启动前后端

完成 `setup.ps1` 后：

```powershell
Set-Location D:\Projects\Video2Doc
.\scripts\start_dev.ps1
```

脚本在当前控制台管理两个 PowerShell Job；按 `Ctrl+C` 会停止并清理两项 Job。

## 测试和构建

### 后端

```powershell
Set-Location D:\Projects\Video2Doc\backend
.\.venv\Scripts\python.exe -m pytest -q
```

测试每次会在唯一的 `backend/data/test-runtime-*` 忽略目录中创建隔离 SQLite 和模拟工作区，覆盖：

- 健康检查、模型能力过滤、处理方案能力校验和设置；
- 任务快照在方案修改后保持不变；
- 三个任务严格串行执行、执行时间不重叠且生成持久化输出；
- 取消等待任务与运行任务；
- 取消与完成的两种竞态顺序都只产生一个一致终态；
- 模拟失败、失败后重试、重试 attempt 增加且保留原始快照；
- OCR 关闭时跳过 OCR 阶段；
- 后端启动时把遗留运行任务识别为 `INTERRUPTED` 并允许重试；
- SQLite 约束拒绝同时存在两个 `PROCESSING` 任务。

### 前端

```powershell
Set-Location D:\Projects\Video2Doc\frontend
pnpm typecheck
pnpm test
pnpm build
```

生产文件输出到被忽略的 `frontend/dist`。

## 配置

根目录 `.env.example` 提供：

- `VIDEO2DOC_DATABASE_URL`：SQLite URL
- `VIDEO2DOC_DATA_DIR`：任务资料目录
- `VIDEO2DOC_INBOX_DIR`：模拟成品 Inbox
- `VIDEO2DOC_CORS_ORIGINS`：允许的本地前端来源
- `VIDEO2DOC_SIMULATION_STEP_SECONDS`：模拟阶段进度间隔
- `ONEAPI_BASE_URL` / `ONEAPI_API_KEY`：仅为后续真实阶段预留，本轮不调用
- `VITE_API_BASE_URL`：前端 API 地址

设置页不会接收或返回 API Key 明文。后端只检查 `ONEAPI_API_KEY` 环境变量是否存在，不把密钥写入数据库、日志、快照或 HTTP 响应。

## 当前可操作功能

- 总览队列计数和最近任务；
- 一次创建多个 URL/本地路径模拟任务；
- 处理方案选择、任务级 OCR 覆盖和测试失败模式；
- 查看等待、处理、完成、失败、取消和中断状态；
- 查看阶段、错误、配置快照、工作区和输出路径；
- 取消等待/运行任务，重试失败/中断任务；
- 模型增删、启停、固定能力标签；
- 处理方案创建、默认设置、按能力过滤模型；
- 应用路径和默认值持久化。

## 尚未实现的真实功能

按计划留到后续阶段：

- yt-dlp、网页解析、URL 元数据和媒体下载；
- FFmpeg 音频提取、视频抽帧和真实文件检查；
- ASR、OCR、OneAPI、Ollama 或任何模型调用；
- ASR/OCR 清洗、内容融合、长文本分块；
- 符合最终内容规范的真实 Obsidian Markdown Pipeline；
- Windows 子进程树取消、原生目录选择、打开文件/文件夹；
- 总结、核心观点、学习笔记、问答、RAG、向量库、知识图谱和自动双链（产品明确不做）；
- 多任务并发、多用户、云部署和浏览器插件。

## 常见问题与已知限制

### 启动时报 `no such table`

先执行迁移，或使用会自动迁移的 `start_backend.ps1`：

```powershell
Set-Location D:\Projects\Video2Doc\backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

### 前端显示无法连接后端

确认健康检查可访问，且 `.env` 中 `VITE_API_BASE_URL` 与后端地址一致。修改 Vite 环境变量后需重启前端。

### pnpm 阻止 esbuild 脚本

仓库的 `frontend/pnpm-workspace.yaml` 只允许 `esbuild` 执行安装脚本。不要删除该最小白名单；它是 Vite 构建所需。

### 为什么运行中任务重启后不自动继续

真实外部命令未来可能有副作用，当前采用保守策略：启动时将遗留 `PROCESSING` 标记为 `INTERRUPTED`，由用户显式重试。等待任务会继续按顺序执行。

### 是否可以同时运行多个后端实例

不可以。第一版是单用户、单后端进程，串行保证以此为边界。未来若支持多实例，需要数据库租约或分布式锁，但不应为当前本地版本引入该复杂度。

### 模拟输出是否代表真实识别内容

不代表。模拟 Markdown 只说明队列和文件写入成功，并明确声明没有媒体处理或模型调用。
