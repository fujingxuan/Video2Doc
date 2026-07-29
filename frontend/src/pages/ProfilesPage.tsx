import { useState } from "react";

import { api } from "../api/client";
import type { GatewayModel, ModelConfig, ProcessingProfile } from "../types";

export function ProfilesPage({
  profiles,
  models,
  gatewayModels,
  refresh,
}: {
  profiles: ProcessingProfile[];
  models: ModelConfig[];
  gatewayModels: GatewayModel[];
  refresh: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [asrGatewayId, setAsrGatewayId] = useState("");
  const [asrGatewayModelId, setAsrGatewayModelId] = useState("");
  const [ocrGatewayId, setOcrGatewayId] = useState("");
  const [ocrGatewayModelId, setOcrGatewayModelId] = useState("");
  const [textModelId, setTextModelId] = useState("");
  const [ocrEnabled, setOcrEnabled] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const asrModels = gatewayModels.filter(item => item.enabled && item.gateway_type === "ASR" && String(item.gateway_id) === asrGatewayId);
  const ocrModels = gatewayModels.filter(item => item.enabled && item.gateway_type === "OCR" && String(item.gateway_id) === ocrGatewayId);
  const asrGateways = Array.from(new Map(gatewayModels.filter(item => item.enabled && item.gateway_type === "ASR").map(item => [item.gateway_id, item.gateway_name])).entries());
  const ocrGateways = Array.from(new Map(gatewayModels.filter(item => item.enabled && item.gateway_type === "OCR").map(item => [item.gateway_id, item.gateway_name])).entries());
  const textModels = models.filter(item => item.enabled && item.capabilities.includes("TEXT_PROCESSING"));

  const create = async () => {
    try {
      await api.createProfile({
        name,
        asr_gateway_model_id: asrGatewayModelId ? Number(asrGatewayModelId) : null,
        ocr_gateway_model_id: ocrGatewayModelId ? Number(ocrGatewayModelId) : null,
        text_model_id: textModelId ? Number(textModelId) : null,
        ocr_enabled: ocrEnabled,
      });
      setName("");
      setMessage(null);
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "创建失败");
    }
  };
  const remove = async (profile: ProcessingProfile) => {
    try { await api.deleteProfile(profile.id); await refresh(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "删除失败"); }
  };
  const update = async (profile: ProcessingProfile, payload: Record<string, unknown>) => {
    try { await api.updateProfile(profile.id, payload); await refresh(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "更新失败"); }
  };

  return <>
    <header className="page-header"><div><p className="eyebrow">可复用工作流配置</p><h1>处理方案</h1><p>ASR/OCR 从独立 Gateway 选择；OneAPI 模型仅用于文本处理。</p></div></header>
    <section className="split"><div className="panel form-panel"><h2>创建方案</h2>
      <label>方案名称<input value={name} onChange={event => setName(event.target.value)} /></label>
      <label>ASR Gateway<select value={asrGatewayId} onChange={event => { setAsrGatewayId(event.target.value); setAsrGatewayModelId(""); }}><option value="">请选择</option>{asrGateways.map(([id, gatewayName]) => <option key={id} value={id}>{gatewayName}</option>)}</select></label>
      <label>ASR 模型<select value={asrGatewayModelId} disabled={!asrGatewayId} onChange={event => setAsrGatewayModelId(event.target.value)}><option value="">请选择</option>{asrModels.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
      <label>OCR Gateway<select value={ocrGatewayId} disabled={!ocrEnabled} onChange={event => { setOcrGatewayId(event.target.value); setOcrGatewayModelId(""); }}><option value="">请选择</option>{ocrGateways.map(([id, gatewayName]) => <option key={id} value={id}>{gatewayName}</option>)}</select></label>
      <label>OCR 模型<select value={ocrGatewayModelId} disabled={!ocrEnabled || !ocrGatewayId} onChange={event => setOcrGatewayModelId(event.target.value)}><option value="">请选择</option>{ocrModels.map(item => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
      <label>文本处理模型（OneAPI）<select value={textModelId} onChange={event => setTextModelId(event.target.value)}><option value="">暂不选择</option>{textModels.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label className="check"><input type="checkbox" checked={ocrEnabled} onChange={event => setOcrEnabled(event.target.checked)} />启用 OCR</label>
      {message && <p className="form-message error-text">{message}</p>}
      <button className="primary" disabled={!name.trim() || !asrGatewayModelId || (ocrEnabled && !ocrGatewayModelId)} onClick={() => void create()}>创建方案</button>
    </div><div className="panel"><h2>现有方案</h2>{profiles.length === 0 ? <div className="state-card">尚未创建方案。</div> : <div className="card-list">{profiles.map(profile => <article key={profile.id}><div><strong>{profile.name} {profile.is_default && <span className="default-label">默认</span>}</strong><div className="profile-models"><span><b>ASR</b>：{profile.asr_selection ? `${profile.asr_selection.gateway_name ?? "模型配置"} / ${profile.asr_selection.display_name}` : "未设置"}</span><span><b>OCR</b>：{profile.ocr_enabled ? (profile.ocr_selection ? `${profile.ocr_selection.gateway_name ?? "模型配置"} / ${profile.ocr_selection.display_name}` : "未设置") : "关闭"}</span><span><b>文本整理</b>：{profile.text_selection?.display_name ?? "未启用"}</span></div><details><summary>查看方案完整模型信息</summary><dl>{profile.asr_selection && <div><dt>ASR 协议</dt><dd>{profile.asr_selection.protocol ?? "—"}</dd></div>}{profile.ocr_selection && <div><dt>OCR 协议</dt><dd>{profile.ocr_selection.protocol ?? "—"}</dd></div>}<div><dt>成品时间戳</dt><dd>{profile.keep_timestamps ? "保留" : "不写入"}</dd></div></dl></details></div><div className="actions">{!profile.is_default && <button onClick={() => void update(profile, { is_default: true })}>设为默认</button>}<button onClick={() => void update(profile, { enabled: !profile.enabled })}>{profile.enabled ? "停用" : "启用"}</button><button disabled={profile.is_builtin} onClick={() => void remove(profile)}>删除</button></div></article>)}</div>}</div></section>
  </>;
}
