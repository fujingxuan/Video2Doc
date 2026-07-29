# Video2Doc 2.1 启动热修报告

## 问题

Video2Doc 2.0 便携版使用 PyInstaller `--windowed` 构建。Windows 无控制台进程中的 `sys.stdout` 和 `sys.stderr` 为 `None`，而 Uvicorn 默认日志格式器启动时会调用 `sys.stderr.isatty()`，最终弹出：

```text
应用启动失败：ValueError: Unable to configure formatter 'default'
```

## 修复

- 启动器创建应用目录后，在导入 Uvicorn 前为缺失的标准输出流绑定 `<app-home>\logs\launcher.log`。
- Uvicorn 明确使用 `use_colors=False`，避免文件日志包含终端颜色控制字符。
- 源码控制台模式保持原有标准输出，不会被重定向。
- 后端、健康接口和前端版本统一升级为 `2.1.0`。

## 验收要求

- 模拟 `sys.stdout=None`、`sys.stderr=None` 时，Uvicorn 默认格式器可正常初始化，输出写入日志文件。
- 后端全量测试、Ruff、前端类型检查/测试/构建和 Alembic 一致性检查全部通过。
- 重建无控制台便携包，并用隔离应用目录验证启动、健康接口、独立窗口关闭和端口释放。

## 验收结果

| 验证项 | 结果 |
| --- | --- |
| 启动器测试 | `6 passed` |
| 后端全量测试 | `87 passed, 2 skipped, 1 warning` |
| Ruff | `All checks passed` |
| 前端 | 类型检查通过、Vitest `1 passed`、生产构建通过 |
| Alembic | 当前版本与 head 均为 `0008_v2_traceable_inputs`，schema check 通过 |
| 无浏览器打包启动 | 健康接口返回 `2.1.0`，退出码 0，端口释放，日志无 formatter 错误 |
| 默认独立窗口 | Edge 应用窗口成功打开，关闭 X 后退出码 0，端口释放 |

两个跳过项仍是需要显式环境开关和真实外部模型环境的打包真实管线、文档复用验收；警告来自 Starlette 对旧 `httpx` 测试接口的弃用提示。

## 便携包

- 目录：`dist/Video2Doc-Portable`
- 压缩包：`dist/Video2Doc-Portable.zip`
- EXE SHA-256：`478A0BD8BDA247C0548527452EA1E0BA8AD48AA606389B4317D91F0BD0BB86B0`
- ZIP SHA-256：`D789EBEE1D8D48F4377F33F72F67536DD814B96A7AD74AF2192B47D9066E7C86`
