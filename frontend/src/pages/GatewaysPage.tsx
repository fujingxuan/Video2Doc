import { useMemo, useState } from "react";

import { api } from "../api/client";
import type { GatewayConfig, GatewayModel, GatewayType } from "../types";

type AuthMode = "environment" | "direct" | "none";

const defaults = {
  ASR: { protocol: "video2doc-asr-http-v1", baseUrl: "http://127.0.0.1:8103" },
  OCR: { protocol: "video2doc-ocr-http-v1", baseUrl: "http://127.0.0.1:8201" },
} as const;

function deploymentLocation(baseUrl: string) {
  try {
    const hostname = new URL(baseUrl).hostname.toLowerCase();
    return ["127.0.0.1", "localhost", "::1"].includes(hostname) ? "本地" : "云端";
  } catch {
    return "未知位置";
  }
}

function modelConnectionStatus(status: string) {
  const labels: Record<string, string> = {
    HEALTHY: "模型可达",
    UNKNOWN: "尚未检测",
    UNREACHABLE: "Gateway 未运行",
    INVALID_CONFIG: "配置无效",
    MISSING_SECRET: "缺少密钥",
    AUTH_FAILED: "认证失败",
    MODEL_NOT_FOUND: "模型目录中不存在",
    RATE_LIMITED: "限流或额度不足",
    TIMEOUT: "连接超时",
    UNHEALTHY: "服务异常",
  };
  return labels[status] ?? status;
}

export function GatewaysPage({
  gatewayType,
  gateways,
  gatewayModels,
  refresh,
}: {
  gatewayType: Exclude<GatewayType, "LLM">;
  gateways: GatewayConfig[];
  gatewayModels: GatewayModel[];
  refresh: () => Promise<void>;
}) {
  const visibleGateways = useMemo(
    () => gateways.filter(item => item.gateway_type === gatewayType),
    [gatewayType, gateways],
  );
  const visibleModels = useMemo(
    () => gatewayModels.filter(item => item.gateway_type === gatewayType),
    [gatewayModels, gatewayType],
  );
  const initial = defaults[gatewayType];
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<string>(initial.protocol);
  const [baseUrl, setBaseUrl] = useState<string>(initial.baseUrl);
  const [authMode, setAuthMode] = useState<AuthMode>("none");
  const [secretEnvName, setSecretEnvName] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState(gatewayType === "ASR" ? "300" : "180");
  const [maxRetries, setMaxRetries] = useState("1");
  const [runtimeDevice, setRuntimeDevice] = useState("cuda");
  const [modelGatewayId, setModelGatewayId] = useState("");
  const [modelName, setModelName] = useState("");
  const [maxChunkSeconds, setMaxChunkSeconds] = useState(gatewayType === "ASR" ? "60" : "");
  const [editingGatewayId, setEditingGatewayId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [checkingModelIds, setCheckingModelIds] = useState<Set<number>>(new Set());
  const [checkingAllModels, setCheckingAllModels] = useState(false);

  const resetGateway = () => {
    const next = defaults[gatewayType];
    setEditingGatewayId(null);
    setName("");
    setProtocol(next.protocol);
    setBaseUrl(next.baseUrl);
    setAuthMode("none");
    setSecretEnvName("");
    setSecretValue("");
    setRuntimeDevice("cuda");
  };

  const saveGateway = async () => {
    try {
      const existing = visibleGateways.find(item => item.id === editingGatewayId);
      const payload: Record<string, unknown> = {
        name,
        gateway_type: gatewayType,
        protocol,
        base_url: baseUrl,
        secret_env_name: authMode === "environment" ? secretEnvName.trim() || null : null,
        settings: {
          ...(existing?.settings ?? {}),
          timeout_seconds: Number(timeoutSeconds),
          max_retries: Number(maxRetries),
          ...(existing?.settings.runtime_kind === "asr" ? { runtime_device: runtimeDevice } : {}),
        },
      };
      if (authMode === "direct" && secretValue.trim()) payload.secret_value = secretValue;
      if (authMode === "none") payload.clear_secret = true;
      if (editingGatewayId === null) {
        await api.createGateway(payload);
      } else {
        await api.updateGateway(editingGatewayId, payload);
      }
      setMessage(existing ? `${gatewayType} Gateway 已更新。` : `${gatewayType} Gateway 已创建。`);
      resetGateway();
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "保存失败");
    }
  };

  const editGateway = (gateway: GatewayConfig) => {
    setEditingGatewayId(gateway.id);
    setName(gateway.name);
    setProtocol(gateway.protocol);
    setBaseUrl(gateway.base_url);
    setAuthMode(gateway.secret_source === "DIRECT" ? "direct" : gateway.secret_source === "ENVIRONMENT" ? "environment" : "none");
    setSecretEnvName(gateway.secret_env_name ?? "");
    setSecretValue("");
    setTimeoutSeconds(String(gateway.settings.timeout_seconds ?? 300));
    setMaxRetries(String(gateway.settings.max_retries ?? 1));
    setRuntimeDevice(String(gateway.settings.runtime_device ?? "cuda"));
    setMessage(`正在编辑 ${gateway.name}`);
  };

  const createModel = async () => {
    try {
      await api.createGatewayModel({
        gateway_id: Number(modelGatewayId),
        model_name: modelName,
        display_name: modelName,
        max_chunk_seconds: gatewayType === "ASR" && maxChunkSeconds ? Number(maxChunkSeconds) : null,
        supports_segment_timestamps: gatewayType === "ASR",
        supports_word_timestamps: false,
        timestamp_granularity: gatewayType === "ASR" ? ["segment"] : [],
      });
      setModelName("");
      setMessage(`${gatewayType} 模型已添加。`);
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

  const runModelCheck = async (model: GatewayModel) => {
    setCheckingModelIds(current => new Set(current).add(model.id));
    try {
      return await api.checkGatewayModel(model.id);
    } finally {
      setCheckingModelIds(current => {
        const next = new Set(current);
        next.delete(model.id);
        return next;
      });
    }
  };

  const checkModel = async (model: GatewayModel) => {
    try {
      const result = await runModelCheck(model);
      setMessage(
        `${model.display_name}: ${modelConnectionStatus(result.status)} · ${result.detail} · ${result.latency_ms} ms`,
      );
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "模型连通性测试失败");
    }
  };

  const checkAllModels = async () => {
    setCheckingAllModels(true);
    let healthy = 0;
    try {
      for (const model of visibleModels) {
        try {
          const result = await runModelCheck(model);
          if (result.status === "HEALTHY") healthy += 1;
        } catch {
          // Continue so one unavailable model does not hide the remaining results.
        }
      }
      await refresh();
      setMessage(`全部 ${gatewayType} 测试完成：${healthy}/${visibleModels.length} 个模型可达。`);
    } finally {
      setCheckingAllModels(false);
    }
  };

  const directSecretMissing = authMode === "direct" && editingGatewayId === null && !secretValue.trim();
  return <>
    <header className="page-header"><div><p className="eyebrow">{gatewayType === "ASR" ? "语音识别连接与模型" : "画面文字识别连接与模型"}</p><h1>{gatewayType}</h1><p>{gatewayType} 使用独立 Gateway；密钥可引用环境变量，也可由 Windows 当前用户加密保存。</p></div></header>
    {message && <div className="notice">{message}</div>}
    <section className="split">
      <div className="panel form-panel"><h2>{editingGatewayId === null ? `添加 ${gatewayType} Gateway` : `编辑 ${gatewayType} Gateway`}</h2>
        <label>名称<input value={name} onChange={event => setName(event.target.value)} /></label>
        <label>协议<input value={protocol} onChange={event => setProtocol(event.target.value)} /></label>
        <label>Base URL<input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} /></label>
        <fieldset><legend>密钥来源</legend><div className="segmented">
          <button className={authMode === "environment" ? "selected" : ""} onClick={() => setAuthMode("environment")}>环境变量</button>
          <button className={authMode === "direct" ? "selected" : ""} onClick={() => setAuthMode("direct")}>直接填写</button>
          <button className={authMode === "none" ? "selected" : ""} onClick={() => setAuthMode("none")}>无需密钥</button>
        </div></fieldset>
        {authMode === "environment" && <label>环境变量名称<input value={secretEnvName} placeholder="例如 DASHSCOPE_API_KEY" onChange={event => setSecretEnvName(event.target.value)} /></label>}
        {authMode === "direct" && <label>API Key<input type="password" autoComplete="new-password" value={secretValue} placeholder={editingGatewayId === null ? "输入密钥" : "留空则保留现有密钥"} onChange={event => setSecretValue(event.target.value)} /><small>API 不会回显直接保存的密钥。</small></label>}
        <label>请求超时（秒）<input type="number" min="1" max="3600" value={timeoutSeconds} onChange={event => setTimeoutSeconds(event.target.value)} /></label>
        <label>最大重试次数<input type="number" min="0" max="10" value={maxRetries} onChange={event => setMaxRetries(event.target.value)} /></label>
        <div className="actions"><button className="primary" disabled={!name.trim() || !baseUrl.trim() || (authMode === "environment" && !secretEnvName.trim()) || directSecretMissing} onClick={() => void saveGateway()}>{editingGatewayId === null ? "创建 Gateway" : "保存 Gateway"}</button>{editingGatewayId !== null && <button onClick={resetGateway}>取消编辑</button>}</div>
      </div>
      {gatewayType === "ASR" && visibleGateways.find(item => item.id === editingGatewayId)?.settings.runtime_kind === "asr" && <div className="panel form-panel">
        <label>本地语音识别设备<select value={runtimeDevice} onChange={event => setRuntimeDevice(event.target.value)}><option value="cuda">NVIDIA 独显（CUDA）</option><option value="cpu">CPU（无独显时使用）</option></select></label>
        <p>随 Gateway 配置保存，在下一次自动启动模型时生效。连接测试会显示实际加载设备与精度。</p>
      </div>}
      <div className="panel form-panel"><h2>手动添加 {gatewayType} 模型</h2>
        <label>所属 Gateway<select value={modelGatewayId} onChange={event => setModelGatewayId(event.target.value)}><option value="">请选择</option>{visibleGateways.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>模型 ID<input value={modelName} onChange={event => setModelName(event.target.value)} /></label>
        {gatewayType === "ASR" && <label>最大离线分块（秒）<input type="number" min="1" value={maxChunkSeconds} onChange={event => setMaxChunkSeconds(event.target.value)} /></label>}
        <button className="primary" disabled={!modelGatewayId || !modelName.trim()} onClick={() => void createModel()}>添加模型</button>
      </div>
    </section>
    <section className="panel"><h2>{gatewayType} Gateway 状态</h2>{visibleGateways.length === 0 ? <div className="state-card">尚未配置 Gateway。</div> : <div className="card-list">{visibleGateways.map(gateway => <article key={gateway.id}><div><strong>{gateway.name}</strong><small>{gateway.protocol} · {gateway.base_url}</small><small>密钥：{gateway.secret_source === "ENVIRONMENT" ? `${gateway.secret_env_name} (${gateway.secret_configured ? "已读取" : "未检测到"})` : gateway.secret_source === "DIRECT" ? `直接保存 (${gateway.secret_configured ? "可读取" : "不可读取"})` : "不需要"} · 超时：{String(gateway.settings.timeout_seconds ?? "默认")} 秒 · 重试：{String(gateway.settings.max_retries ?? "默认")}</small><small>健康：{gateway.health_status}</small></div><div className="actions"><button onClick={() => editGateway(gateway)}>编辑</button><button onClick={() => void check(gateway)}>连接测试</button><button disabled={gateway.protocol.includes("dashscope")} onClick={() => void discover(gateway)}>模型发现</button><button onClick={() => void api.updateGateway(gateway.id, { enabled: !gateway.enabled }).then(refresh)}>{gateway.enabled ? "停用" : "启用"}</button></div></article>)}</div>}</section>
    <section className="panel"><h2>{gatewayType} 模型</h2><p>逐模型测试会先检查 Gateway 与认证，再确认目标模型出现在模型目录中；不会上传用户媒体。</p><div className="actions"><button disabled={checkingAllModels || visibleModels.length === 0} onClick={() => void checkAllModels()}>{checkingAllModels ? "正在逐个测试…" : `逐个测试全部 ${gatewayType} 模型`}</button></div>{visibleModels.length === 0 ? <div className="state-card">尚未添加模型。</div> : <div className="card-list">{visibleModels.map(model => <article key={model.id}><div><strong>{model.display_name}</strong><small>{model.gateway_name} · {model.protocol}</small><small>部署：{deploymentLocation(model.gateway_base_url)} · 连接：{modelConnectionStatus(model.health_status)} · 语言：{model.languages.length ? model.languages.join(" / ") : "Provider 未声明"}</small>{gatewayType === "ASR" && <small>分块：{model.supports_streaming ? "连续流" : `${model.max_chunk_seconds ?? "未限制"} 秒 + 5 秒重叠`} · 时间戳：{model.supports_word_timestamps ? "词级" : model.supports_segment_timestamps ? "句级" : "无"}</small>}</div><div className="actions"><button disabled={checkingAllModels || checkingModelIds.has(model.id)} onClick={() => void checkModel(model)}>{checkingModelIds.has(model.id) ? "测试中…" : "测试连通性"}</button><button onClick={() => void api.updateGatewayModel(model.id, { enabled: !model.enabled }).then(refresh)}>{model.enabled ? "停用" : "启用"}</button></div></article>)}</div>}</section>
  </>;
}
