# Video2Doc OCR 与时间线融合验收报告

更新日期：2026-07-27

## 验收范围

本报告只覆盖本阶段：

```text
真实视频
→ 媒体时间轴抽帧
→ 视觉去重
→ 真实 OCR
→ 原始响应与规范化结果
→ OCR 稳定事件
→ ASR/OCR/帧统一时间线
```

不包含最终 Markdown、Obsidian Inbox、文本大模型编辑、总结、RAG、问答或桌面打包。

## 自动化回归

```text
Ruff: passed
Pytest: 69 passed, 1 Starlette/httpx deprecation warning
Alembic head: 0006_ocr_fusion_artifacts
Alembic check: no new upgrade operations
Frontend typecheck: passed
Frontend Vitest: 1 passed
Frontend production build: passed
PowerShell AST: passed
Bash syntax: passed
git diff --check: passed
```

自动化覆盖迁移、任务快照、三种抽帧策略边界、媒体时间戳、视觉去重、取消 partial 清理、OCR HTTP 取消、规范化、稳定事件、增量更新、融合来源保留、配置变化禁用复用、失败/重试、API 和数据库 Worker。真实验收不替代这些测试。

## 真实抽帧专项

输入：

```text
D:\Video2Doc-Test\sample.mp4
时长: 542.349546 秒
策略: hybrid
固定间隔: 60 秒
场景阈值: 0.50
最大候选帧: 12
```

结果：

```text
候选帧: 10
保留帧: 10
丢弃帧: 0
时间范围: 0.000000 - 524.700000 秒
时间戳单调: 是
全部位于媒体时长内: 是
完整扫描视频时间线: 是
源视频 SHA-256 前后不变: 是
```

该输入在较宽间隔下没有产生足够相似的连续画面，因此不能据此推断真实视频的去重率。相似帧去重由确定性图像单元测试覆盖；没有伪造去重数量。

## 真实数据库 Worker 全链路

任务 1：

```text
输入: D:\Video2Doc-Test\sample.mp4
Pipeline: REAL
ASR: Whisper large-v3-turbo / CUDA
OCR: PaddleOCR 3.7.0 / PP-OCRv5 server / CPU
attempt 1: WinGet ffprobe ACL 导致 WORKSPACE_ERROR，保留历史
attempt 2: COMPLETED
attempt 2 处理耗时: 313.7 秒
```

最终结果：

```text
媒体时长: 542.349546 秒
ASR 分块: 22
ASR segment: 192
候选帧: 9
保留帧: 9
丢弃帧: 0
帧时间范围: 0.000000 - 482.133312 秒
OCR 原始响应: 9
OCR 事件: 9
OCR 稳定重复合并: 0
Frame event: 9
融合事件总数: 210
工作区产物: 158
.part/.tmp 残留: 0
源视频 SHA-256 前后不变: 是
```

所有阶段均由数据库 Worker 执行：

```text
INPUT_VALIDATE → MEDIA_ACQUIRE → MEDIA_PROBE
→ AUDIO_EXTRACT → AUDIO_VALIDATE → AUDIO_CHUNK
→ ASR_TRANSCRIBE → ASR_COMBINE → ASR_VALIDATE
→ FRAME_EXTRACT → FRAME_DEDUP
→ OCR_RECOGNIZE → OCR_TIMELINE
→ FUSION_BUILD → FUSION_VALIDATE
```

## 真实失败恢复与 OCR 复用

任务 2 使用同一视频、最多 3 个候选帧。第一帧 OCR 规范化结果落盘后，验收主动停止本地 OCR Gateway：

```text
attempt 1:
  status: FAILED
  stage: OCR_RECOGNIZE
  error_code: OCR_GATEWAY_UNAVAILABLE
  已完成 OCR 帧: 1
```

重启 Gateway 后通过 API 调用 `/retry`：

```text
attempt 2:
  status: COMPLETED
  帧: 2
  OCR 文字块: 53
  OCR 复用: 1/2
  OCR 原始响应: 2
  OCR 事件: 2
  ASR segment: 192
  融合事件: 196
  工作区产物: 131
  临时残留: 0
  attempt 历史: [1, 2]
```

这证明真实 Provider 失败不会硬编码成功，重试会复用请求指纹一致且原始响应仍存在的逐帧 OCR，并继续完成剩余 OCR 与融合。

## 可追溯性检查

- 每个保留帧有原始图像、规范化图像、媒体时间戳、哈希、场景分数和决策。
- 每个 OCR 结果有 Provider、模型、帧 ID、媒体时间、原始响应引用、文字块、置信度和位置。
- OCR 稳定事件保留来源帧/图像与首次/最后出现时间。
- 融合事件保持 ASR 音频来源和 OCR 画面来源；相似文字没有跨来源删除。
- `artifacts.json` 索引帧、OCR 和融合产物。
- 单文件产物 API 拒绝越出任务工作区。
- 输入视频只读引用，验收前后 SHA-256 均为 `8ed1dfa24ed7d011a1d38a5643438359b57c083028007c87a8a88803be259541`。

## 未声明的结论

- 没有人工标注稿，因此不声明 OCR 或 ASR 准确率。
- 此视频的 9 帧/2 帧验收没有产生视觉重复，不把 0 丢弃解释为去重算法无效或准确。
- 没有执行最终 Markdown 或 Obsidian 导出。
- 没有测试桌面打包或多后端并发。
