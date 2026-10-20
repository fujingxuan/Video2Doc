# Video2Doc 本地 OCR 模型部署报告

更新日期：2026-07-27

## 状态结论

| 项目 | 状态 | 证据 |
| --- | --- | --- |
| OCR Gateway 架构 | 已完成 | `video2doc-ocr-http-v1`、健康检查、模型发现、数据库模型 |
| PaddleOCR Runtime 安装 | 已完成 | 隔离环境导入及 PaddlePaddle CPU `run_check()` 通过 |
| 模型文件 | 已下载 | PP-OCRv5 server detection/recognition，共 6 文件、165.2 MiB |
| 真实单帧推理 | 已完成 | `sample.mp4` 实际帧返回 40 个文本块 |
| 数据库 Worker 全链路 | 已完成 | 真实视频 task 1、task 2，详见验收报告 |
| GPU OCR | 未部署 | 当前明确使用 CPU，避免与 ASR 抢占 12 GiB 显存 |

## 选择

本阶段选择 PaddlePaddle 3.3.0 CPU、PaddleOCR 3.7.0、PaddleX 3.7.2，模型为：

```text
PP-OCRv5_server_det
PP-OCRv5_server_rec
```

Gateway 对外暴露逻辑模型名 `PP-OCRv5_server`。选择原因：

- PP-OCRv5 server 面向高精度场景，支持中文、英文和常见混排。
- Provider 能返回逐块文字、置信度、矩形框和多边形，满足可追溯要求。
- Windows CPU Runtime 不占用本地 Whisper/Qwen/FireRed ASR 的显存。
- 独立 Gateway 将 Paddle 依赖与 FastAPI 后端隔离，后端只依赖统一 HTTP 协议。

官方参考：

- [PaddlePaddle Windows pip 安装](https://www.paddlepaddle.org.cn/documentation/docs/install/pip/windows-pip_en.html)
- [PaddleOCR 通用 OCR Pipeline](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)
- [PP-OCRv5 多语言文字识别](https://paddlepaddle.github.io/PaddleOCR/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html)

## 实际环境

```text
OS: Windows
Python: 3.12.13
PaddlePaddle: 3.3.0 CPU
PaddleOCR: 3.7.0
PaddleX: 3.7.2
NumPy: 2.3.5
OpenCV: 4.10.0
FastAPI: 0.140.0
Uvicorn: 0.51.0
Environment: D:\Models\Video2Doc\envs\paddleocr
Model cache: D:\Models\Video2Doc\ocr\cache\official_models
Runtime temp: D:\Models\Video2Doc\ocr\runtime\temp
Logs: D:\Models\Video2Doc\logs\paddleocr
Bind: 127.0.0.1:8201
```

模型加载并完成推理后的观测值：

```text
Working set: 约 580 MiB
Private memory: 约 1,264 MiB
Model files: 165.2 MiB
GPU memory: 0 MiB（CPU Runtime）
```

这些是单次观测，不是容量承诺。

## 安装、启动、验证和停止

```powershell
Set-Location D:\Projects\Video2Doc

.\scripts\model_runtime\setup_ocr_gateway.ps1
.\scripts\model_runtime\start_ocr_gateway.ps1

.\scripts\model_runtime\test_ocr_gateway.ps1 `
  -Image D:\path\to\frame.jpg `
  -Output D:\path\to\gateway-response.json

.\scripts\model_runtime\stop_ocr_gateway.ps1
```

安装脚本默认：

- 在 `D:\Models\Video2Doc\envs\paddleocr` 创建独立 venv。
- 从 Paddle 官方 CPU index 安装 `paddlepaddle==3.3.0`。
- 安装仓库固定的 `paddleocr==3.7.0` 与 Gateway 依赖。
- 运行 PaddlePaddle CPU 自检。
- 不修改后端 `.venv`，不下载或重新部署 ASR。

启动脚本保存 venv 启动器 PID 和实际监听 PID。停止脚本终止进程树并确认 8201 不再监听。

## 真实单帧结果

输入：

```text
D:\Video2Doc-Test\sample.mp4
抽取帧：包含中英文网页文字和中文字幕的实际画面
```

结果：

```text
Provider: paddleocr-3.7.0
Model: PP-OCRv5_server
文本块: 40
含置信度: 40/40
含矩形框: 40/40
含多边形: 40/40
文字字符数: 861
置信度范围: 0.1933 - 0.9986
置信度均值: 0.8403
Provider 耗时: 23.562 秒
```

没有人工标注真值，因此不报告或推测准确率。

## 已解决的部署问题

首次真实推理在 PaddlePaddle 3.3.0 Windows CPU oneDNN 路径触发：

```text
NotImplementedError:
ConvertPirAttribute2RuntimeAttribute not support
pir::ArrayAttribute<pir::DoubleAttribute>
```

Gateway 明确设置 `enable_mkldnn=False`，启动环境同时设置 `FLAGS_use_mkldnn=0`。修复后同一帧真实推理成功。

强制终止 Gateway 时，请求级 `finally` 无法执行，可能遗留以 `video2doc-ocr-` 开头的上传。Gateway 现在每次启动只清除 runtime temp 中自身前缀的陈旧文件；真实故障注入后的残留已在重启时清除，其他文件不会删除。

## 限制

- 当前单进程串行 OCR，优先质量和确定性，不面向高吞吐。
- `PP-OCRv5_server` 对小字、压缩画面和复杂代码/公式仍可能误识别；原始响应、位置和置信度均保留供追溯。
- 当前使用 Provider 返回顺序作为读取顺序，没有构建文档级图文版面树。
- 视觉公式只保留 OCR 实际返回，不根据上下文猜测或补写。
- 首帧含模型加载，耗时显著高于健康检查；健康 `model_loaded=false` 只表示尚未加载，不表示部署失败。

## 旧 Docker PaddleOCR 扫描与适配

2026-07-27 对 Docker 元数据和常见项目目录执行了只读扫描，范围包括：

```text
docker context/info/ps/ps -a/images/compose/network/volume
D:\Projects
D:\Models
D:\Tools
D:\Data（不存在）
D:\Docker（不存在）
Windows 当前用户的常见项目路径
WSL 当前用户的常见项目路径
```

扫描时没有运行中或已停止的 PaddleOCR 容器，也没有活动 compose 项目。找到：

```text
D:\Tools\paddle ocr\compose.yaml
ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/
  paddleocr-vl:latest-nvidia-gpu-sm120-offline
  paddleocr-genai-vllm-server:latest-nvidia-gpu-sm120-offline
Docker volume: paddleocr_model_cache
```

这是一套 PaddleOCR-VL 镜像/compose 残留，不是当前可调用的文字检测/识别 API。历史日志显示 host pipeline 因未知 `pipeline_config` 失败，扫描时 8118 未监听。因此没有启动、停止、删除或修改该容器、镜像、网络、volume 和 compose 文件，也没有把“找到镜像”描述成“找到可用服务”。

为复用未来恢复的旧服务，应用新增 `paddlex-layout-parsing-v1` 协议适配器，支持官方 `/layout-parsing` 请求/响应；并提供 `deploy/docker/paddleocr-vl` 中的可选 Video2Doc 自有 compose 示例。已有外部服务可直接注册 Base URL，`managed_lifecycle=false` 时应用只做 health/recognition，不管理其生命周期。

主验收继续使用当前本地 Windows CPU `PP-OCRv5_server` Gateway。Docker 适配器由受控响应自动化测试覆盖；由于旧 Docker 服务没有运行，本轮没有把它记为真实 Docker OCR 推理通过。
