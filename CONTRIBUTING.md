# 贡献指南

欢迎通过 [GitHub Issues](https://github.com/fujingxuan/Video2Doc/issues) 报告问题或提出建议，通过 Pull Request 提交改进。较大的功能或架构调整请先开 Issue 说明使用场景。

## 开发准备

按 [README](README.md#源码开发环境要求) 配置 Windows、Python、Node.js、pnpm 和媒体工具，并执行 `scripts\setup.ps1`。使用 `.env.example` 创建本机 `.env`，只填写自己的开发凭据。ASR/OCR 模型运行时独立部署；日常自动化回归无需真实模型或付费服务。

## 提交改动

- 让改动围绕一个清晰的问题，说明修改前后的行为和验证结果；涉及界面时附截图。
- 保持原文忠实性、来源追溯与失败回退；不要引入自动总结或补写视频外知识。
- 为实质行为变更增加或调整相应测试，并更新受影响的使用文档。
- 不要提交密钥、`.env`、Cookie、数据库、用户媒体、任务日志、模型权重、虚拟环境或构建产物。示例材料应允许公开分发。
- 新增第三方组件时，记录其来源和许可，并更新 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 验证

后端在 `backend` 中执行：

```powershell
.\.venv\Scripts\python.exe -m ruff check --no-cache app tests
.\.venv\Scripts\python.exe -m pytest -q
```

前端在 `frontend` 中执行：

```powershell
pnpm typecheck
pnpm test
pnpm build
```

媒体工具或打包变更还应运行 README 中对应的脚本检查与便携版验收。需要公网、GPU、真实模型或凭据的验证，请在 PR 中明确环境、步骤和未覆盖的范围。

提交贡献表示你有权将该内容按项目 [MIT 许可证](LICENSE) 提供。第三方内容仍适用其原有许可证。
