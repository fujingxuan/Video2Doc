import { useMemo, useState } from "react";

import { api } from "../api/client";
import type { GatewayModel, ProcessingProfile } from "../types";

export function NewTaskPage({ profiles, gatewayModels, onCreated }: { profiles: ProcessingProfile[]; gatewayModels: GatewayModel[]; onCreated: () => Promise<void> }) {
  const [sourceType, setSourceType] = useState<"URL" | "LOCAL_FILE">("URL");
  const [sources, setSources] = useState("");
  const [profileId, setProfileId] = useState("");
  const [asrGatewayId, setAsrGatewayId] = useState("");
  const [asrGatewayModelId, setAsrGatewayModelId] = useState("");
  const [ocrEnabled, setOcrEnabled] = useState(false);
  const [ocrGatewayId, setOcrGatewayId] = useState("");
  const [ocrGatewayModelId, setOcrGatewayModelId] = useState("");
  const [frameStrategy, setFrameStrategy] = useState<"fixed_interval" | "scene_change" | "hybrid">("hybrid");
  const [frameInterval, setFrameInterval] = useState(15);
  const [sceneThreshold, setSceneThreshold] = useState(0.35);
  const [maxCandidateFrames, setMaxCandidateFrames] = useState(240);
  const [fusionWindow, setFusionWindow] = useState(8);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const asrGateways = useMemo(() => Array.from(new Map(gatewayModels.filter(item => item.gateway_type === "ASR" && item.enabled).map(item => [item.gateway_id, item.gateway_name])).entries()), [gatewayModels]);
  const asrModels = useMemo(() => gatewayModels.filter(item => item.gateway_type === "ASR" && item.enabled && String(item.gateway_id) === asrGatewayId), [asrGatewayId, gatewayModels]);
  const ocrGateways = useMemo(() => Array.from(new Map(gatewayModels.filter(item => item.gateway_type === "OCR" && item.enabled).map(item => [item.gateway_id, item.gateway_name])).entries()), [gatewayModels]);
  const ocrModels = useMemo(() => gatewayModels.filter(item => item.gateway_type === "OCR" && item.enabled && String(item.gateway_id) === ocrGatewayId), [gatewayModels, ocrGatewayId]);
  const selectedProfile = useMemo(() => profiles.find(item => String(item.id) === profileId), [profileId, profiles]);

  const submit = async () => {
    const inputs = sources.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    if (!inputs.length) { setMessage("请至少输入一个 URL 或本地媒体绝对路径。"); return; }
    if (sourceType === "URL" && inputs.some(value => !/^https?:\/\//i.test(value))) { setMessage("URL 输入必须使用 http 或 https。"); return; }
    if (sourceType === "LOCAL_FILE" && inputs.some(value => !/^(?:[a-z]:[\\/]|\\\\)/i.test(value))) { setMessage("本地媒体必须填写 Windows 绝对路径。"); return; }
    if (ocrEnabled && !ocrGatewayModelId && !selectedProfile?.ocr_gateway_model_id) { setMessage("启用 OCR 时必须选择 OCR Gateway 模型，或使用已配置 OCR 的处理方案。"); return; }
    setBusy(true); setMessage(null);
    try {
      const tasks = await api.createTasks({
        pipeline_mode: "REAL",
        inputs: inputs.map(source => ({ source, source_type: sourceType })),
        profile_id: profileId ? Number(profileId) : null,
        asr_gateway_model_id: asrGatewayModelId ? Number(asrGatewayModelId) : null,
        ocr_gateway_model_id: ocrGatewayModelId ? Number(ocrGatewayModelId) : null,
        config_overrides: {
          ocr_enabled: ocrEnabled,
          frame_sampling_strategy: frameStrategy,
          ocr_frame_interval_seconds: frameInterval,
          scene_change_threshold: sceneThreshold,
          max_candidate_frames: maxCandidateFrames,
          fusion_window_seconds: fusionWindow,
        },
      });
      setSources(""); setMessage(`已创建 ${tasks.length} 个真实处理任务。`); await onCreated();
    } catch (error) { setMessage(error instanceof Error ? error.message : "创建失败"); }
    finally { setBusy(false); }
  };

  return <><header className="page-header"><div><p className="eyebrow">真实媒体输入</p><h1>新建任务</h1><p>每行一个输入；本地文件与 URL 不会混在同一批中。</p></div></header>
    <section className="panel form-panel">
      <div className="segmented"><button className={sourceType === "URL" ? "selected" : ""} onClick={() => { setSourceType("URL"); setSources(""); }}>视频 URL</button><button className={sourceType === "LOCAL_FILE" ? "selected" : ""} onClick={() => { setSourceType("LOCAL_FILE"); setSources(""); }}>本地媒体路径</button></div>
      <label>{sourceType === "URL" ? "媒体 URL" : "Windows 本地媒体绝对路径"}<textarea rows={7} value={sources} onChange={event => setSources(event.target.value)} placeholder={sourceType === "URL" ? "https://www.youtube.com/watch?v=...\nhttps://www.bilibili.com/video/..." : "D:\\Media\\lecture.mp4\nD:\\Media\\audio.m4a"}/></label>
      {sourceType === "LOCAL_FILE" && <div className="notice">浏览器不能安全返回任意本地绝对路径，因此当前使用路径输入；后端仍会检查路径和文件存在性，并以 ffprobe 结果判断媒体有效性。</div>}
      <div className="form-grid"><label>处理方案<select value={profileId} onChange={event => { const value = event.target.value; setProfileId(value); const profile = profiles.find(item => String(item.id) === value); if (profile) setOcrEnabled(profile.ocr_enabled); }}><option value="">默认 / 上次使用</option>{profiles.filter(item => item.enabled).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>ASR Gateway<select value={asrGatewayId} onChange={event => { setAsrGatewayId(event.target.value); setAsrGatewayModelId(""); }}><option value="">使用处理方案中的 Gateway</option>{asrGateways.map(([id, gatewayName]) => <option key={id} value={id}>{gatewayName}</option>)}</select></label><label>ASR 模型<select value={asrGatewayModelId} disabled={!asrGatewayId} onChange={event => setAsrGatewayModelId(event.target.value)}><option value="">使用处理方案中的模型</option>{asrModels.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label></div>
      <label className="check"><input type="checkbox" checked={ocrEnabled} onChange={event => setOcrEnabled(event.target.checked)} />启用真实视频抽帧、OCR 与 ASR/OCR 时间线融合</label>
      {ocrEnabled && <><div className="form-grid"><label>OCR Gateway<select value={ocrGatewayId} onChange={event => { setOcrGatewayId(event.target.value); setOcrGatewayModelId(""); }}><option value="">使用处理方案中的 Gateway</option>{ocrGateways.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><label>OCR 模型<select value={ocrGatewayModelId} disabled={!ocrGatewayId} onChange={event => setOcrGatewayModelId(event.target.value)}><option value="">使用处理方案中的模型</option>{ocrModels.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label></div><details><summary>OCR 抽帧与融合参数</summary><div className="form-grid"><label>抽帧策略<select value={frameStrategy} onChange={event => setFrameStrategy(event.target.value as typeof frameStrategy)}><option value="hybrid">固定间隔 + 画面变化</option><option value="fixed_interval">固定间隔</option><option value="scene_change">画面变化</option></select></label><label>固定间隔（秒）<input type="number" min={1} max={3600} step={1} value={frameInterval} onChange={event => setFrameInterval(Number(event.target.value))}/></label><label>场景阈值<input type="number" min={0} max={1} step={0.01} value={sceneThreshold} onChange={event => setSceneThreshold(Number(event.target.value))}/></label><label>最大候选帧<input type="number" min={1} max={10000} step={1} value={maxCandidateFrames} onChange={event => setMaxCandidateFrames(Number(event.target.value))}/></label><label>ASR 关联窗口（秒）<input type="number" min={0} max={300} step={1} value={fusionWindow} onChange={event => setFusionWindow(Number(event.target.value))}/></label></div></details></>}
      <div className="notice">离线 ASR 按模型能力分块并默认保留 5 秒重叠，在结构化合并层去重；Fun-ASR 连续流不会重复发送音频。</div>
      {message && <p className="form-message">{message}</p>}<div className="actions"><button className="primary" disabled={busy} onClick={() => void submit()}>{busy ? "正在创建…" : "加入串行队列"}</button><button disabled={!sources} onClick={() => setSources("")}>清空输入</button></div>
    </section></>;
}
