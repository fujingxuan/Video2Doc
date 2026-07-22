import { useState } from "react";

import { api } from "../api/client";
import type { GatewayConfig, GatewayModel, GatewayType } from "../types";

function deploymentLocation(baseUrl: string) {
  try {
    const hostname = new URL(baseUrl).hostname.toLowerCase();
    return ["127.0.0.1", "localhost", "::1"].includes(hostname) ? "本地" : "云端";
  } catch {
    return "未知位置";
  }
}

export function GatewaysPage({
  gateways,
  gatewayModels,
  refresh,
}: {
  gateways: GatewayConfig[];
  gatewayModels: GatewayModel[];
  refresh: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [gatewayType, setGatewayType] = useState<GatewayType>("ASR");
  const [protocol, setProtocol] = useState("video2doc-asr-http-v1");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8104");
  const [secretEnvName, setSecretEnvName] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("300");
  const [maxRetries, setMaxRetries] = useState("1");
  const [modelGatewayId, setModelGatewayId] = useState("");
  const [modelName, setModelName] = useState("");
  const [maxChunkSeconds, setMaxChunkSeconds] = useState("60");
  const [editingGatewayId, setEditingGatewayId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const saveGateway = async () => {
    try {
      const existing = gateways.find(item => item.id === editingGatewayId);
      const payload = {
        name,
        gateway_type: gatewayType,
        protocol,
        base_url: baseUrl,
        secret_env_name: secretEnvName.trim() || null,
        settings: {
          ...(existing?.settings ?? {}),
          timeout_seconds: Number(timeoutSeconds),
          max_retries: Number(maxRetries),
        },
      };
      if (editingGatewayId === null) {
        await api.createGateway(payload);
      } else {
        await api.updateGateway(editingGatewayId, payload);
      }
      setName("");
      setEditingGatewayId(null);
      setMessage(existing ? "Gateway 已更新。" : "Gateway 已创建。");
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "保存失败");
    }
  };
  const editGateway = (gateway: GatewayConfig) => {
    setEditingGatewayId(gateway.id);
    setName(gateway.name);
    setGatewayType(gateway.gateway_type);
    setProtocol(gateway.protocol);
    setBaseUrl(gateway.base_url);
    setSecretEnvName(gateway.secret_env_name ?? "");
    setTimeoutSeconds(String(gateway.settings.timeout_seconds ?? 300));
    setMaxRetries(String(gateway.settings.max_retries ?? 1));
    setMessage(`正在编辑 ${gateway.name}`);
  };
  const cancelEdit = () => {
    setEditingGatewayId(null);
    setName("");
    setMessage(null);
  };
  const createModel = async () => {
    try {
      await api.createGatewayModel({
        gateway_id: Number(modelGatewayId),
        model_name: modelName,
        display_name: modelName,
        max_chunk_seconds: maxChunkSeconds ? Number(maxChunkSeconds) : null,
        supports_segment_timestamps: true,
        supports_word_timestamps: false,
        timestamp_granularity: ["segment"],
      });
      setModelName("");
      setMessage("Gateway 模型已添加。");
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "模型添加失败");
    }
  };
  const check = async (gateway: GatewayConfig) => {
    try {
      const result = await api.checkGateway(gateway.id);
      setMessage(`${gateway.name}: ${result.status}${result.detail ? ` · ${result.detail}` : ""}`);
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "连接测试失败");
    }
  };
  const discover = async (gateway: GatewayConfig) => {
    try {
      await api.discoverGateway(gateway.id);
      setMessage(`${gateway.name}: 模型发现完成。`);
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "模型发现失败");
    }
  };

  return <>
    <header className="page-header"><div><p className="eyebrow">独立连接与能力目录</p><h1>ASR / OCR Gateway</h1><p>Gateway 保存协议和环境变量名称；模型只引用 Gateway，不保存密钥值。</p></div></header>
    {message && <div className="notice">{message}</div>}
    <section className="split">
      <div className="panel form-panel"><h2>{editingGatewayId === null ? "添加 Gateway" : "编辑 Gateway"}</h2>
        <label>类型<select value={gatewayType} onChange={event => { const value = event.target.value as GatewayType; setGatewayType(value); setProtocol(value === "ASR" ? "video2doc-asr-http-v1" : "video2doc-ocr-http-v1"); }}><option value="ASR">ASR</option><option value="OCR">OCR</option></select></label>
        <label>名称<input value={name} onChange={event => setName(event.target.value)} /></label>
        <label>协议<input value={protocol} onChange={event => setProtocol(event.target.value)} /></label>
        <label>Base URL<input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} /></label>
        <label>密钥环境变量名称<input value={secretEnvName} placeholder="可留空；例如 DASHSCOPE_API_KEY" onChange={event => setSecretEnvName(event.target.value)} /></label>
        <label>请求超时（秒）<input type="number" min="1" max="3600" value={timeoutSeconds} onChange={event => setTimeoutSeconds(event.target.value)} /></label>
        <label>最大重试次数<input type="number" min="0" max="10" value={maxRetries} onChange={event => setMaxRetries(event.target.value)} /></label>
        <div className="actions"><button className="primary" disabled={!name.trim() || !baseUrl.trim()} onClick={() => void saveGateway()}>{editingGatewayId === null ? "创建 Gateway" : "保存 Gateway"}</button>{editingGatewayId !== null && <button onClick={cancelEdit}>取消编辑</button>}</div>
      </div>
      <div className="panel form-panel"><h2>手动添加模型</h2>
        <label>所属 Gateway<select value={modelGatewayId} onChange={event => setModelGatewayId(event.target.value)}><option value="">请选择</option>{gateways.map(item => <option key={item.id} value={item.id}>{item.gateway_type} · {item.name}</option>)}</select></label>
        <label>模型 ID<input value={modelName} onChange={event => setModelName(event.target.value)} /></label>
        <label>最大离线分块（秒）<input type="number" min="1" value={maxChunkSeconds} onChange={event => setMaxChunkSeconds(event.target.value)} /></label>
        <button className="primary" disabled={!modelGatewayId || !modelName.trim()} onClick={() => void createModel()}>添加模型</button>
      </div>
    </section>
    <section className="panel"><h2>Gateway 状态</h2><div className="card-list">{gateways.map(gateway => <article key={gateway.id}><div><strong>{gateway.gateway_type} · {gateway.name}</strong><small>{gateway.protocol} · {gateway.base_url}</small><small>密钥：{gateway.secret_env_name ? `${gateway.secret_env_name} (${gateway.secret_configured ? "已配置" : "未检测到"})` : "不需要"} · 超时：{String(gateway.settings.timeout_seconds ?? "默认")} 秒 · 重试：{String(gateway.settings.max_retries ?? "默认")}</small><small>健康：{gateway.health_status}</small></div><div className="actions"><button onClick={() => editGateway(gateway)}>编辑</button><button onClick={() => void check(gateway)}>连接测试</button><button disabled={gateway.protocol.includes("dashscope")} onClick={() => void discover(gateway)}>模型发现</button><button onClick={() => void api.updateGateway(gateway.id, { enabled: !gateway.enabled }).then(refresh)}>{gateway.enabled ? "停用" : "启用"}</button></div></article>)}</div></section>
    <section className="panel"><h2>Gateway 模型</h2><div className="card-list">{gatewayModels.map(model => <article key={model.id}><div><strong>{model.display_name}</strong><small>{model.gateway_type} · {model.gateway_name} · {model.protocol}</small><small>部署：{deploymentLocation(model.gateway_base_url)} · 运行：{model.health_status === "HEALTHY" ? "已响应" : model.health_status} · 语言：{model.languages.length ? model.languages.join(" / ") : "Provider 未声明"}</small><small>分块：{model.supports_streaming ? "连续流" : `${model.max_chunk_seconds ?? "未限制"} 秒 + 5 秒重叠`} · 时间戳：{model.supports_word_timestamps ? "词级" : model.supports_segment_timestamps ? "句级" : "无"} · 健康：{model.health_status}</small></div><div className="actions"><button onClick={() => void api.updateGatewayModel(model.id, { enabled: !model.enabled }).then(refresh)}>{model.enabled ? "停用" : "启用"}</button></div></article>)}</div></section>
  </>;
}
