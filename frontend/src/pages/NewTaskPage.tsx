import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type {
  AppSettings,
  GatewayModel,
  ModelConfig,
  ProcessingProfile,
} from "../types";

interface Props {
  profiles: ProcessingProfile[];
  gatewayModels: GatewayModel[];
  models: ModelConfig[];
  onCreated: () => Promise<void>;
}

export function NewTaskPage({ profiles, gatewayModels, models, onCreated }: Props) {
  const [sourceType, setSourceType] = useState<"URL" | "LOCAL_FILE">("URL");
  const [sources, setSources] = useState("");
  const [profileId, setProfileId] = useState("");
  const [asrGatewayId, setAsrGatewayId] = useState("");
  const [asrGatewayModelId, setAsrGatewayModelId] = useState("");
  const [ocrEnabled, setOcrEnabled] = useState(false);
  const [ocrGatewayId, setOcrGatewayId] = useState("");
  const [ocrGatewayModelId, setOcrGatewayModelId] = useState("");
  const [textEditEnabled, setTextEditEnabled] = useState(false);
  const [textModelId, setTextModelId] = useState("");
  const [exportVault, setExportVault] = useState("");
  const [exportInbox, setExportInbox] = useState("Inbox");
  const [frameStrategy, setFrameStrategy] = useState<
    "fixed_interval" | "scene_change" | "hybrid"
  >("hybrid");
  const [frameInterval, setFrameInterval] = useState(15);
  const [sceneThreshold, setSceneThreshold] = useState(0.35);
  const [maxCandidateFrames, setMaxCandidateFrames] = useState(240);
  const [fusionWindow, setFusionWindow] = useState(8);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);

  useEffect(() => {
    api.settings()
      .then(value => {
        setSettings(value);
        setTextEditEnabled(value.default_text_edit_enabled);
        setExportVault(value.obsidian_vault_path ?? "");
        setExportInbox(value.inbox_path && !/^(?:[a-z]:[\\/]|\\\\)/i.test(value.inbox_path)
          ? value.inbox_path
          : "Inbox");
      })
      .catch(error => setMessage(error instanceof Error ? error.message : "设置加载失败"));
  }, []);

  const asrGateways = useMemo(
    () => Array.from(new Map(
      gatewayModels
        .filter(item => item.gateway_type === "ASR" && item.enabled)
        .map(item => [item.gateway_id, item.gateway_name]),
    ).entries()),
    [gatewayModels],
  );
  const asrModels = useMemo(
    () => gatewayModels.filter(
      item => item.gateway_type === "ASR"
        && item.enabled
        && String(item.gateway_id) === asrGatewayId,
    ),
    [asrGatewayId, gatewayModels],
  );
  const ocrGateways = useMemo(
    () => Array.from(new Map(
      gatewayModels
        .filter(item => item.gateway_type === "OCR" && item.enabled)
        .map(item => [item.gateway_id, item.gateway_name]),
    ).entries()),
    [gatewayModels],
  );
  const ocrModels = useMemo(
    () => gatewayModels.filter(
      item => item.gateway_type === "OCR"
        && item.enabled
        && String(item.gateway_id) === ocrGatewayId,
    ),
    [gatewayModels, ocrGatewayId],
  );
  const textModels = useMemo(
    () => models.filter(
      item => item.enabled && item.capabilities.includes("TEXT_PROCESSING"),
    ),
    [models],
  );
  const selectedProfile = useMemo(
    () => profiles.find(item => String(item.id) === profileId),
    [profileId, profiles],
  );

  const pickFiles = async () => {
    setPicking(true);
    setMessage(null);
    try {
      const result = await api.selectMedia();
      if (result.paths.length) {
        setSourceType("LOCAL_FILE");
        setSources(result.paths.join("\n"));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法打开文件选择器");
    } finally {
      setPicking(false);
    }
  };

  const submit = async () => {
    const inputs = sources.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    if (!inputs.length) {
      setMessage("请至少选择或输入一个视频、音频或媒体 URL。");
      return;
    }
    if (sourceType === "URL" && inputs.some(value => !/^https?:\/\//i.test(value))) {
      setMessage("URL 输入必须使用 http 或 https。");
      return;
    }
    if (
      sourceType === "LOCAL_FILE"
      && inputs.some(value => !/^(?:[a-z]:[\\/]|\\\\)/i.test(value))
    ) {
      setMessage("本地媒体必须使用 Windows 绝对路径。");
      return;
    }
    if (ocrEnabled && !ocrGatewayModelId && !selectedProfile?.ocr_gateway_model_id) {
      setMessage("启用 OCR 时必须选择 OCR Gateway 模型，或使用已配置 OCR 的处理方案。");
      return;
    }
    if (textEditEnabled && !textModelId && !selectedProfile?.text_model_id) {
      setMessage("启用轻度文本整理时必须选择 TEXT_PROCESSING 模型。");
      return;
    }
    if (exportVault && !/^(?:[a-z]:[\\/]|\\\\)/i.test(exportVault)) {
      setMessage("Obsidian Vault 必须填写 Windows 绝对路径。");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const tasks = await api.createTasks({
        pipeline_mode: "REAL",
        inputs: inputs.map(source => ({ source, source_type: sourceType })),
        profile_id: profileId ? Number(profileId) : null,
        asr_gateway_model_id: asrGatewayModelId
          ? Number(asrGatewayModelId)
          : null,
        ocr_gateway_model_id: ocrGatewayModelId
          ? Number(ocrGatewayModelId)
          : null,
        text_model_id: textModelId ? Number(textModelId) : null,
        export_vault_path: exportVault || null,
        export_inbox_relative_path: exportInbox || "Inbox",
        config_overrides: {
          ocr_enabled: ocrEnabled,
          text_edit_enabled: textEditEnabled,
          frame_sampling_strategy: frameStrategy,
          ocr_frame_interval_seconds: frameInterval,
          scene_change_threshold: sceneThreshold,
          max_candidate_frames: maxCandidateFrames,
          fusion_window_seconds: fusionWindow,
        },
      });
      setSources("");
      setMessage(`已创建 ${tasks.length} 个完整转换任务。`);
      await onCreated();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建失败");
    } finally {
      setBusy(false);
    }
  };

  return <>
    <header className="page-header">
      <div>
        <p className="eyebrow">视频 / 音频 / 网页 URL</p>
        <h1>新建完整转换任务</h1>
        <p>任务将依次完成媒体、ASR、可选 OCR、忠实整理、Markdown 与 Obsidian 导出。</p>
      </div>
    </header>
    <section className="panel form-panel">
      <div className="segmented">
        <button
          className={sourceType === "URL" ? "selected" : ""}
          onClick={() => { setSourceType("URL"); setSources(""); }}
        >
          视频网页 URL
        </button>
        <button
          className={sourceType === "LOCAL_FILE" ? "selected" : ""}
          onClick={() => { setSourceType("LOCAL_FILE"); setSources(""); }}
        >
          本地视频 / 音频
        </button>
      </div>
      {sourceType === "LOCAL_FILE" && <button onClick={() => void pickFiles()} disabled={picking}>
        {picking ? "等待选择…" : "选择本地媒体文件…"}
      </button>}
      <label>
        {sourceType === "URL" ? "媒体 URL（每行一个）" : "本地媒体绝对路径（每行一个）"}
        <textarea
          rows={7}
          value={sources}
          onChange={event => setSources(event.target.value)}
          placeholder={sourceType === "URL"
            ? "https://www.youtube.com/watch?v=...\nhttps://www.bilibili.com/video/..."
            : "D:\\Media\\lecture.mp4\nD:\\Media\\audio.m4a"}
        />
      </label>
      <div className="form-grid">
        <label>处理方案
          <select
            value={profileId}
            onChange={event => {
              const value = event.target.value;
              setProfileId(value);
              const profile = profiles.find(item => String(item.id) === value);
              if (profile) {
                setOcrEnabled(profile.ocr_enabled);
                setTextEditEnabled(Boolean(profile.text_model_id));
              }
            }}
          >
            <option value="">默认 / 上次使用</option>
            {profiles.filter(item => item.enabled).map(item => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <label>ASR Gateway
          <select
            value={asrGatewayId}
            onChange={event => {
              setAsrGatewayId(event.target.value);
              setAsrGatewayModelId("");
            }}
          >
            <option value="">使用处理方案中的 Gateway</option>
            {asrGateways.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
        <label>ASR 模型
          <select
            value={asrGatewayModelId}
            disabled={!asrGatewayId}
            onChange={event => setAsrGatewayModelId(event.target.value)}
          >
            <option value="">使用处理方案中的模型</option>
            {asrModels.map(item => (
              <option key={item.id} value={item.id}>{item.display_name}</option>
            ))}
          </select>
        </label>
      </div>

      <label className="check">
        <input
          type="checkbox"
          checked={ocrEnabled}
          onChange={event => setOcrEnabled(event.target.checked)}
        />
        启用真实视频抽帧、OCR 与 ASR/OCR 时间线融合
      </label>
      {ocrEnabled && <>
        <div className="form-grid">
          <label>OCR Gateway
            <select
              value={ocrGatewayId}
              onChange={event => {
                setOcrGatewayId(event.target.value);
                setOcrGatewayModelId("");
              }}
            >
              <option value="">使用处理方案中的 Gateway</option>
              {ocrGateways.map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
          </label>
          <label>OCR 模型
            <select
              value={ocrGatewayModelId}
              disabled={!ocrGatewayId}
              onChange={event => setOcrGatewayModelId(event.target.value)}
            >
              <option value="">使用处理方案中的模型</option>
              {ocrModels.map(item => (
                <option key={item.id} value={item.id}>{item.display_name}</option>
              ))}
            </select>
          </label>
        </div>
        <details>
          <summary>OCR 抽帧与融合参数</summary>
          <div className="form-grid">
            <label>抽帧策略
              <select
                value={frameStrategy}
                onChange={event => setFrameStrategy(event.target.value as typeof frameStrategy)}
              >
                <option value="hybrid">固定间隔 + 画面变化</option>
                <option value="fixed_interval">固定间隔</option>
                <option value="scene_change">画面变化</option>
              </select>
            </label>
            <label>固定间隔（秒）
              <input
                type="number"
                min={1}
                max={3600}
                value={frameInterval}
                onChange={event => setFrameInterval(Number(event.target.value))}
              />
            </label>
            <label>场景阈值
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={sceneThreshold}
                onChange={event => setSceneThreshold(Number(event.target.value))}
              />
            </label>
            <label>最大候选帧
              <input
                type="number"
                min={1}
                max={10000}
                value={maxCandidateFrames}
                onChange={event => setMaxCandidateFrames(Number(event.target.value))}
              />
            </label>
            <label>ASR 关联窗口（秒）
              <input
                type="number"
                min={0}
                max={300}
                value={fusionWindow}
                onChange={event => setFusionWindow(Number(event.target.value))}
              />
            </label>
          </div>
        </details>
      </>}

      <label className="check">
        <input
          type="checkbox"
          checked={textEditEnabled}
          onChange={event => setTextEditEnabled(event.target.checked)}
        />
        启用 OneAPI 忠实轻度整理（响应不合规则自动回退原文）
      </label>
      {textEditEnabled && <label>文本模型
        <select value={textModelId} onChange={event => setTextModelId(event.target.value)}>
          <option value="">使用处理方案中的模型</option>
          {textModels.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>}

      <fieldset>
        <legend>Obsidian 导出目标</legend>
        <div className="form-grid">
          <label>Vault 绝对路径
            <input
              value={exportVault}
              onChange={event => setExportVault(event.target.value)}
              placeholder="D:\\Notes\\MyVault"
            />
          </label>
          <label>Inbox 相对路径
            <input
              value={exportInbox}
              onChange={event => setExportInbox(event.target.value)}
              placeholder="Inbox"
            />
          </label>
        </div>
        {!exportVault && <small>
          未指定时使用应用设置中的 Vault；若也未设置，则使用本地测试 Inbox。
        </small>}
      </fieldset>

      <div className="notice">
        无文本模型也能完成完整 Markdown；应用不会生成摘要、核心观点、问答、RAG 或知识库索引。
        当前 OneAPI 密钥状态：{settings?.api_key_configured ? "已配置" : "未配置"}。
      </div>
      {message && <p className="form-message">{message}</p>}
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => void submit()}>
          {busy ? "正在创建…" : "加入串行队列"}
        </button>
        <button disabled={!sources} onClick={() => setSources("")}>清空输入</button>
      </div>
    </section>
  </>;
}
