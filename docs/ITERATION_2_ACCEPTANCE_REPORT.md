# Video2Doc 2.0 迭代验收报告

## 交付范围

- 方案列表直接展示 ASR、OCR、文本处理所用网关、模型和协议。
- 任务详情按处理步骤保存并展示实际模型调用；本地工具和确定性步骤明确标记为未调用模型。
- 新建任务支持导入 SRT、VTT、ASS、SSA 或 TXT 中文字幕，并跳过音频提取、切块和 ASR。
- Windows 启动器使用独立 Edge 应用窗口，关闭窗口会同时退出由该启动器管理的后端进程。
- 新任务可直接选择导出目录；Markdown 默认不写元数据时间、段落时间、截图时间或追溯表时间。

## 自动化验证

| 验证项 | 结果 |
| --- | --- |
| Python 全量测试 | `86 passed, 2 skipped, 1 warning` |
| Ruff | `All checks passed`（使用 `--no-cache` 避开受管目录缓存权限） |
| 前端 TypeScript | 通过 |
| 前端 Vitest | `1 passed` |
| 前端生产构建 | 通过，42 个模块 |
| Alembic 当前版本 | `0008_v2_traceable_inputs (head)` |
| Alembic schema check | `No new upgrade operations detected` |
| 空目录源码启动检查 | 自动完成 0001 到 0008 迁移并退出 0 |
| 字幕真实管线集成测试 | 证明未执行音频标准化和 ASR，导出完整无时间戳 Markdown |
| 独立窗口关闭测试 | 健康检查成功，关闭 Edge 窗口后启动器退出 0 且端口释放 |

两个跳过项是需要显式环境开关和真实外部模型环境的打包真实管线、文档复用验收；警告来自 Starlette 对旧 `httpx` 测试接口的弃用提示。

## 便携包

- 目录：`dist/Video2Doc-Portable`
- 压缩包：`dist/Video2Doc-Portable.zip`
- EXE SHA-256：`25B9F4406D03F9D4B5371ADC7F5077E9413B2A7E71D5779943128C6AA170AADE`
- ZIP SHA-256：`06F3859131BA8A737B9566F032541FA623CD12C040980E87B61CEDF041069C4A`

当前受管 Windows 的 Application Control 会拦截本地新生成且未签名的 EXE，因此未把该机器上的打包 EXE 启动记为通过。相同启动器源码已完成实际独立窗口、关闭窗口、退出后端和释放端口的端到端验证；发布到启用应用控制的环境前仍需代码签名或管理员放行。
