# Video2Doc 2.2 单实例与连接稳定性验收报告

## 问题证据

2.1 现场日志显示：第一个实例在 `127.0.0.1:8765` 正常运行；第二次启动识别到“Video2Doc 已在运行”后，却等待新 Edge 进程并调用现有实例的 shutdown 接口。原后端随即进入 `Shutting down`，留下的两个页面因此同时显示 `Failed to fetch`。

侧边栏版本号还硬编码为 `v1.0`，与后端和发行包版本不一致。

## 2.2 修复

- Windows 桌面启动使用系统命名互斥锁；同一登录会话只允许一个 Video2Doc 主进程。
- 第二次启动在资源加载、数据库迁移和打开 Edge 前正常退出，不创建第二个窗口。
- 删除“检测到现有实例后打开新窗口，窗口结束后关闭现有后端”的危险路径。
- 启动器跟踪实际 Edge 顶层窗口句柄；关闭 X 后不再等待可能继续驻留的 Edge 启动进程。
- 网络不可达时显示中文诊断信息，不再直接暴露浏览器的 `Failed to fetch`。
- 侧边栏从 `frontend/package.json` 读取版本，显示与发行包一致的 `v2.2.0`。
- 后端包、FastAPI、健康接口和前端版本统一为 `2.2.0`。

## 验收要求

- 启动第一个打包 EXE 后，健康接口持续返回 `2.2.0`。
- 第一次启动只产生一个独立应用窗口。
- 再启动同一 EXE 时，第二个进程退出码为 0，窗口数量仍为 1。
- 第二次启动后，第一个后端仍健康，不出现 shutdown 或 `Failed to fetch`。
- 关闭唯一窗口后，第一个启动器退出码为 0，端口释放。
- 通过后端全量测试、Ruff、前端类型检查/测试/构建、PowerShell AST 和 Alembic 一致性检查。

## 验收结果

| 验证项 | 结果 |
| --- | --- |
| 启动器测试 | `9 passed`，包含真实 Windows 命名互斥锁和窗口句柄监控 |
| 后端全量测试 | `90 passed, 2 skipped, 1 warning` |
| Ruff | `All checks passed` |
| 前端 | 类型检查通过、Vitest `1 passed`、生产构建通过 |
| PowerShell | 便携构建脚本 AST 通过 |
| Alembic | 当前版本与 head 均为 `0008_v2_traceable_inputs`，schema check 通过 |
| 视觉验收 | 侧边栏显示 `v2.2.0`、页面为“实时连接”、浏览器控制台无错误 |
| 第一次打包启动 | 健康接口返回 `2.2.0`，独立窗口数为 1 |
| 第二次打包启动 | 第二个进程退出码 0，窗口数仍为 1，第一个后端持续健康 |
| 关闭唯一窗口 | 启动器完成正常退出，端口释放，无残留 Video2Doc 或专用 Edge 进程 |

两个跳过项是需要显式环境开关和真实外部模型环境的打包真实管线、文档复用验收；警告来自 Starlette 对旧 `httpx` 测试接口的弃用提示。

## 便携包

- 目录：`dist/Video2Doc-Portable`
- 压缩包：`dist/Video2Doc-Portable.zip`
- EXE SHA-256：`3EF9082F8A1F8FBB573DE661E253B73BC64BE523AEF47886B592C201C79B0EDB`
- ZIP SHA-256：`68C4A9A43B3424D5913F66ED9D8E89F1ACCE63F46C3802F9EE7040D6E8B8FC75`
