# Video2Doc 应用完成验收报告

日期：2026-07-27

## 结论与证据边界

本报告严格区分：

- **实现完成**：代码和迁移已存在并接入实际 Worker/API/前端；
- **自动化通过**：由默认离线测试验证；
- **真实通过**：对指定源媒体、真实外部工具和真实模型执行；
- **受控适配器通过**：协议用固定响应验证，未声称外部服务真实运行；
- **未验证/已知限制**：没有足够证据时明确列出。

产品闭环已经实现：本地/URL 媒体、音频标准化、ASR、抽帧、OCR、融合、忠实文档、Markdown、Obsidian 导出和 Windows 单入口。没有加入 RAG、问答、自动总结、Embedding、向量数据库或语义搜索。

## 真实新推理与打包版全链路

源媒体：

```text
D:\Video2Doc-Test\sample.mp4
字节数：125,129,714
处理前 SHA-256：8ED1DFA24ED7D011A1D38A5643438359B57C083028007C87A8A88803BE259541
处理后 SHA-256：8ED1DFA24ED7D011A1D38A5643438359B57C083028007C87A8A88803BE259541
```

运行形态：`dist\Video2Doc-Portable\Video2Doc.exe`，隔离 app-home，真实 FFmpeg/ffprobe/yt-dlp，DashScope Fun-ASR 和本地 Windows CPU PP-OCRv5 Gateway。

首次 attempt 执行了新的模型推理：

```text
ASR runtime：dashscope-fun-asr
ASR 模型：fun-asr-realtime-2026-02-28
ASR 分块：1，复用 0
候选帧：2，保留 2，丢弃 0
OCR 模型：PP-OCRv5_server
OCR 帧：2，复用 0
首次 attempt 耗时：676.298 秒
```

首次在 `OBSIDIAN_EXPORT` 故意遇到不存在的 Vault，得到 `EXPORT_VAULT_NOT_FOUND`，没有伪装成功。创建隔离测试 Vault 后通过公开 retry API 重试：

```text
第二 attempt 耗时：0.647 秒
OCR 帧：2，复用 2
最终状态：COMPLETED
独立取消任务最终状态：CANCELLED
```

最终结构：

```text
融合事件：23（ASR 19、OCR 2、frame 2）
OCR blocks：53
Document sections：2
Document blocks：23
Source references：23（ASR 19、OCR 2，另有 frame 引用）
文本整理：无模型确定性路径，accepted=true，fallback=false
Markdown：8,851 字符、23 个时间戳、2 张相对图片
YAML frontmatter：可解析
```

导出结果：

```text
D:\Video2Doc-Test\Obsidian-Test-Vault\
  Video2Doc-Fresh-Inference-Acceptance-20260728d\
  vault\Inbox\sample packaged fresh real acceptance.md
```

验证了 Markdown 非空、时间戳在媒体时长内、图片相对路径可解析且文件存在、ASR/OCR 引用均保留、无任务临时绝对路径、没有用摘要替代原文。报告 JSON 位于同一验收根目录的 `fresh-acceptance-summary.json`。

## 旧任务兼容与上游复用

另用快照 schema v4 的既有真实产物执行完整文档/导出阶段：

```text
ASR chunks：22
ASR segments：192
OCR frames：2
Document blocks：196
Markdown：32,561 字符
时间戳：195
图片：2
sidecars：3
artifacts：83
```

该运行没有把复用描述成新模型推理；其价值是验证旧任务兼容、有效产物复用和新版文档/导出阶段。报告位于：

```text
D:\Video2Doc-Test\Obsidian-Test-Vault\
  Video2Doc-App-Completion-Acceptance-20260728\
  acceptance-summary.json
```

## Docker PaddleOCR

只读扫描找到两个离线 PaddleOCR-VL 镜像、`paddleocr_model_cache` volume 和 `D:\Tools\paddle ocr\compose.yaml`，但没有运行/停止的相关容器或活动 compose，8118 未监听。没有修改或删除任何旧 Docker 资源。

实现了 `/layout-parsing` 协议适配器和可选 Video2Doc 自有 compose；适配器由受控响应自动化验证。旧 Docker 服务本轮没有真实启动，因此明确记录为“未完成真实 Docker OCR 推理”。主链路真实 OCR 使用本地 PP-OCRv5。

## Windows 发行

实现完成并已实际构建：

```text
D:\Projects\Video2Doc\dist\Video2Doc-Portable\Video2Doc.exe
D:\Projects\Video2Doc\dist\Video2Doc-Portable.zip
```

最终重建结果：

```text
便携目录文件：144
便携目录大小：56.075 MiB
Video2Doc.exe SHA-256：
59EF11D41DA2E863ABCE512DDFA99AD95357C5D222A0802E441F9A465EBB004D
Video2Doc-Portable.zip：30,877,479 bytes
ZIP SHA-256：
86EC2E38F39667995A5D0D2F6D6D2FFD9FB4BBA0631BC556CD3F941F3CFEBEFE
```

真实新推理验收所用打包版验证了迁移、前端静态资源、后端健康、已有任务读取、任务创建、工具状态、正常 shutdown 和端口释放。最后一次 UI 文案更新后重新构建的发行包也在隔离 app-home 中完成全迁移、health、前端 HTTP 200、模拟任务 `COMPLETED`、系统工具状态、shutdown 和 8894 端口释放。

浏览器内可视检查受当前自动化浏览器安全策略拒绝 localhost；前端 production build、静态响应、API 和打包真实任务已验证，但不把这一项写成浏览器视觉回归通过。

## 自动化与工具回归

最终命令结果：

- Ruff：通过；
- 完整 Pytest：`79 passed, 2 skipped, 1 warning`；两个 skip 是显式 opt-in 的真实验收，均已单独运行通过；
- Alembic：项目数据库 `0007_document_exports (head)`，heads/check 通过；新隔离数据库 0001→0007、downgrade 到 0006、再 upgrade 到 0007 通过；
- 前端 TypeScript：通过；
- Vitest：`1 passed`；
- production build：通过；
- PowerShell AST：21 个脚本，0 个解析错误；
- WSL Bash：`run_asr_gateway.sh` 的 `bash -n` 最终复核通过；
- 媒体工具脚本回归和真实版本命令：通过；FFmpeg/ffprobe 8.1.2、yt-dlp 2026.07.04；
- PyInstaller：最终重建通过；
- 最终发行包资源/迁移 `--check-only`：通过；
- `git diff --check`：通过，仅有预期的 LF→CRLF 工作区提示。

这里不报告 WER、CER、OCR 准确率或文本整理准确率，因为没有人工真值。

## 已知限制与未验证项

- 旧 Docker PaddleOCR 没有活动服务，未完成真实 Docker 请求/故障恢复。
- 自动化浏览器受安全策略拒绝 localhost，未完成像素级视觉验收。
- 便携版不是 MSI、自动升级器或独立 WebView。
- OCR 复杂版面使用 Provider 阅读顺序和时间文档块，不重建像素级完整版面。
- 单后端、单任务并发是当前有意设计。
- 正式 Obsidian Vault 路径未知；真实导出只写入独立测试 Vault。
