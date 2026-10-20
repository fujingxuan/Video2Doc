# Video2Doc 3.4.1 双格式导出

本文记录此前本机构建的验收。公开发行包改用外部 yt-dlp，并补齐开源许可材料；其文件哈希以 [Releases](https://github.com/fujingxuan/Video2Doc/releases/tag/v3.4.1) 附带的 `SHA256SUMS.txt` 为准。

2026-10-13 完成。每次最终导出同时生成同名 `.md` 和 `.docx`；Word 从该次 Markdown 的可见内容生成，使用最终确认稿并内嵌本地画面。支持标题、段落、粗斜体、链接、列表、代码及表格；原文中的公式文本保持文本形式。本次没有引入模型调用或重新转写。

Markdown、Word、附件和追溯文件作为同一导出包执行重名检查、提交和回滚。单独存在同名 Word 也会触发冲突策略；提交失败会恢复覆盖前的文件。Word 路径保存在现有导出记录的 JSON 中，无新增数据库迁移；永久删除按记录清理生成文件并保护其他任务引用的 Word，不根据文件名推测所有权。

任务详情新增 Word 路径与“打开最终 Word”按钮。历史导出记录没有 Word 时不显示按钮，不自动修改历史成品；新版本中的后续最终导出会生成两种格式，包括尚未导出的人工校对任务。

验证证据：

- 66 项针对性回归测试通过：文档导出、正文/图片/格式、同名保护、部分提交回滚、跨任务保护、回收站、人工校对、完整转换与 API。
- 前端 TypeScript 类型检查与 Vite 生产构建通过，新增模块 Ruff 检查通过。
- 使用文档技能的 `render_docx.py` 将最终 Word 示例渲染为 2 页并逐页检查，中文无缺字，图片与标题/说明同页，表格和正文无遮挡。此证据来自 LibreOffice 渲染，不宣称 Microsoft Word 原生渲染。
- 新便携版在 `.acceptance/word-export-3.4.1/app-home` 独立运行，合成初稿经真实人工校对提交 API 进入 COMPLETED；工作区和导出目录均生成 Markdown、Word 与内嵌图片。未访问用户任务数据库，未启动 ASR/OCR 模型。
- 打包后的任务详情页面实际显示 3.4.1、Word 路径和打开按钮，浏览器控制台错误 0。通过该隔离实例的 shutdown API 正常退出。

当前安装目录：`dist/Video2Doc-Portable`；压缩包：`dist/Video2Doc-Portable.zip`。

ZIP SHA-256：`653160F8EE04A40D0119243FDF6EC88DC1436C37FC4172E5B87994E1CCA4F5F3`。
