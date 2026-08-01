# Video2Doc 2.3.0 三类模型、托管 Gateway 与画面字幕验收报告

验收日期：2026-08-01（Asia/Shanghai）

## 交付范围

- Edge 独立应用窗口增加 `--disable-extensions` 与
  `--disable-component-extensions-with-background-pages`，阻止隔离 Profile 中的第三方扩展欢迎页抢占窗口。
- 侧边栏将旧“模型管理 / Gateway”拆成 LLM、OCR、ASR 三个入口。
- LLM 提供本地 OneAPI、OpenAI、DeepSeek、阿里云百炼/Qwen、智谱 GLM、Moonshot/Kimi、SiliconFlow、OpenRouter、Gemini、火山方舟以及自定义 OpenAI 兼容入口。
- LLM、OCR、ASR 密钥均支持环境变量或直接填写。直接填写值用 Windows 当前用户 DPAPI 加密；API、日志和任务快照只返回来源与可用状态，不返回密钥。
- 内置本地 ASR/OCR Gateway 在任务需要且端口未监听时自动启动，并把启动失败转换为可操作的结构化错误和日志位置。
- 链接字幕顺序为 yt-dlp 字幕轨、B 站播放器字幕接口、可选的视频下方烧录字幕 OCR。最后一种方式保留抽帧、裁剪、OCR 原始响应、规范化结果和 SRT。

## 用户样例实测

样例：`https://www.bilibili.com/video/BV1bsgx6EEUf?t=0.7`

- yt-dlp 2026.07.04 只报告 `danmaku xml`，没有可下载字幕轨。
- B 站 `/x/web-interface/view` 返回 `cid=40231964510`；未登录的
  `/x/player/wbi/v2` 与 `/x/player/v2` 均返回空字幕列表。
- 720p 画面确认字幕为直接烧录在视频下方的中文字幕。
- 353.385 秒视频按 1 秒间隔处理 354 帧；PaddleOCR 3.7.0 / PP-OCRv5 server 完成 354 次识别。
- 底部大字号与同基线筛选去除了推文截图中的英文标签、小数字等噪声，最终生成 186 条 SRT。
- 首条为“当推特把字数翻到4000”，末条为“一句话被转了八千次”。
- 产物：`.acceptance/embedded-caption-real/00_source/subtitles/source.embedded-ocr.srt`；SHA-256
  `13ce00061194a53eebd3baaa66ad0215fc503c3fce8a73d521a79238e4f00028`。

附带的 JiJiDown 静态重建包仅做列表与文本读取，没有运行其中程序。它确认了 B 站 view/player API 路由，但说明恢复的 Rust 方法体不完整；Video2Doc 的播放器字幕请求与 JSON 转 SRT 是独立实现，不依赖或复制未知实现体。

## ASR 故障闭环

故障前 `127.0.0.1:8103` 没有监听，历史任务因此记录
`ASR_GATEWAY_UNAVAILABLE / All connection attempts failed`。

修正后应用检测到本地 Whisper Gateway，自动定位
`D:\Models\Video2Doc\envs\whisper` 的实际 WSL 环境并启动服务。健康接口返回
`provider=whisper`、`device=cuda`。真实 45 秒中文 WAV 使用 `large-v3` 返回：

- 6 个句级分段；
- 212 个词级时间戳；
- 热启动推理 4.905 秒；
- 真实响应保存在 `.acceptance/asr-whisper-real.json`。

## 质量门与发行包

- 后端：98 passed，2 个显式 opt-in 的旧版真实验收测试跳过；Ruff 全通过。
- 前端：TypeScript 检查通过，Vitest 1 passed，Vite production build 通过。
- Alembic 新数据库从 0001 升级到 `0009_three_model_credentials` 成功。
- 2.3.0 隔离便携包构建成功，包含 `model_runtime` 启动组件；`--check-only` 返回 ok。
- 交付 ZIP：`dist/Video2Doc-Portable-2.3.0.zip`（隔离构建原件保留在
  `.acceptance/portable-dist/Video2Doc-Portable.zip`），31,003,785 bytes，SHA-256
  `A5776EE00EFA7F1BED5974C26F2C74D99127F66E3A3883054C9F2C1385A13220`。
- 当前旧版 `dist/Video2Doc-Portable/Video2Doc.exe` 在验收时仍由 PID 30128 使用，因此没有强制终止用户窗口或覆盖该目录。

## GUI 证据边界

启动参数和单元测试已验证 Edge 扩展禁用参数。Windows 应用控制授权在现场验收时超时，因此本轮没有取得新 2.3.0 Edge 窗口的人工可见截图；不能把这一项表述为已完成视觉验收。隔离包和后端健康检查均已完成，关闭旧版窗口后可直接运行隔离包做最终目视确认。
