import { useState } from "react";

import { api } from "../api/client";
import type { ModelConfig, ModelHealth } from "../types";

type AuthMode = "environment" | "direct" | "none";

const providerPresets = [
  { id: "oneapi", label: "OneAPI（本地）", baseUrl: "http://127.0.0.1:3000/v1", env: "ONEAPI_API_KEY" },
  { id: "openai", label: "OpenAI", baseUrl: "https://api.openai.com/v1", env: "OPENAI_API_KEY" },
  { id: "deepseek", label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", env: "DEEPSEEK_API_KEY" },
  { id: "qwen", label: "阿里云百炼 / Qwen", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", env: "DASHSCOPE_API_KEY" },
  { id: "zhipu", label: "智谱 GLM", baseUrl: "https://open.bigmodel.cn/api/paas/v4", env: "ZHIPU_API_KEY" },
  { id: "moonshot", label: "Moonshot / Kimi", baseUrl: "https://api.moonshot.cn/v1", env: "MOONSHOT_API_KEY" },
  { id: "siliconflow", label: "SiliconFlow", baseUrl: "https://api.siliconflow.cn/v1", env: "SILICONFLOW_API_KEY" },
  { id: "openrouter", label: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", env: "OPENROUTER_API_KEY" },
  { id: "gemini", label: "Google Gemini", baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai", env: "GEMINI_API_KEY" },
  { id: "volcengine", label: "火山方舟", baseUrl: "https://ark.cn-beijing.volces.com/api/v3", env: "ARK_API_KEY" },
  { id: "custom", label: "自定义 OpenAI 兼容入口", baseUrl: "", env: "" },
] as const;

function modelHealthLabel(status: string) {
  const labels: Record<string, string> = {
    UNKNOWN: "尚未检测",
    HEALTHY: "可用",
    INVALID_CONFIG: "配置无效",
    MISSING_SECRET: "缺少密钥",
    AUTH_FAILED: "认证失败",
    MODEL_NOT_FOUND: "模型或接口不存在",
    RATE_LIMITED: "限流或额度不足",
    TIMEOUT: "连接超时",
    UNREACHABLE: "无法连接",
    UNHEALTHY: "服务异常",
    INVALID_RESPONSE: "响应格式无效",
  };
  return labels[status] ?? status;
}

export function ModelsPage({
  models,
  refresh,
}: {
  models: ModelConfig[];
  refresh: () => Promise<void>;
}) {
  const [provider, setProvider] = useState("oneapi");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState<string>(providerPresets[0].baseUrl);
  const [notes, setNotes] = useState("");
  const [vision, setVision] = useState(false);
  const [authMode, setAuthMode] = useState<AuthMode>("environment");
  const [secretEnvName, setSecretEnvName] = useState<string>(providerPresets[0].env);
  const [secretValue, setSecretValue] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [healthResults, setHealthResults] = useState<Record<number, ModelHealth>>({});
  const [checkingIds, setCheckingIds] = useState<Set<number>>(new Set());
  const [checkingAll, setCheckingAll] = useState(false);

  const textModels = models.filter(item => item.capabilities.includes("TEXT_PROCESSING"));

  const chooseProvider = (next: string) => {
    const preset = providerPresets.find(item => item.id === next);
    setProvider(next);
    if (preset) {
      setBaseUrl(preset.baseUrl);
      setSecretEnvName(preset.env);
      setAuthMode(preset.env ? "environment" : "none");
      setSecretValue("");
    }
  };

  const reset = () => {
    setProvider("oneapi");
    setName("");
    setBaseUrl(providerPresets[0].baseUrl);
    setNotes("");
    setVision(false);
    setAuthMode("environment");
    setSecretEnvName(providerPresets[0].env);
    setSecretValue("");
    setEditingId(null);
  };

  const save = async () => {
    try {
      const payload: Record<string, unknown> = {
        name: name.trim(),
        provider,
        base_url: baseUrl.trim(),
        notes,
        capabilities: vision ? ["TEXT_PROCESSING", "VISION"] : ["TEXT_PROCESSING"],
        secret_env_name: authMode === "environment" ? secretEnvName.trim() || null : null,
      };
      if (authMode === "direct" && secretValue.trim()) payload.secret_value = secretValue;
      if (authMode === "none") payload.clear_secret = true;
      if (editingId === null) {
        await api.createModel(payload);
      } else {
        await api.updateModel(editingId, payload);
      }
      setMessage(editingId === null ? "LLM 模型入口已添加。" : "LLM 模型入口已更新。");
      reset();
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "保存失败");
    }
  };

  const edit = (model: ModelConfig) => {
    setEditingId(model.id);
    setProvider(model.provider || "custom");
    setName(model.name);
    setBaseUrl(model.base_url || "");
    setNotes(model.notes ?? "");
    setVision(model.capabilities.includes("VISION"));
    setAuthMode(
      model.secret_source === "DIRECT"
        ? "direct"
        : model.secret_source === "ENVIRONMENT"
          ? "environment"
          : "none",
    );
    setSecretEnvName(model.secret_env_name ?? "");
    setSecretValue("");
    setMessage(`正在编辑 ${model.name}`);
  };

  const remove = async (id: number) => {
    try {
      await api.deleteModel(id);
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "删除失败");
    }
  };

  const toggleEnabled = async (model: ModelConfig) => {
    try {
      await api.updateModel(model.id, { enabled: !model.enabled });
      await refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "更新失败");
    }
  };

  const runHealthCheck = async (model: ModelConfig) => {
    setCheckingIds(current => new Set(current).add(model.id));
    try {
      const result = await api.checkModel(model.id);
      setHealthResults(current => ({ ...current, [model.id]: result }));
      return result;
    } finally {
      setCheckingIds(current => {
        const next = new Set(current);
        next.delete(model.id);
        return next;
      });
    }
  };

  const checkModel = async (model: ModelConfig) => {
    try {
      const result = await runHealthCheck(model);
      setMessage(
        `${model.name}: ${modelHealthLabel(result.status)} · ${result.detail} · ${result.latency_ms} ms`,
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "模型连通性测试失败");
    }
  };

  const checkAllModels = async () => {
    setCheckingAll(true);
    let healthy = 0;
    try {
      for (const model of textModels) {
        try {
          const result = await runHealthCheck(model);
          if (result.status === "HEALTHY") healthy += 1;
        } catch {
          // Continue so one unavailable model does not hide the remaining results.
        }
      }
      setMessage(`全部 LLM 测试完成：${healthy}/${textModels.length} 个模型可用。`);
    } finally {
      setCheckingAll(false);
    }
  };

  const directSecretMissing = authMode === "direct" && editingId === null && !secretValue.trim();
  return <>
    <header className="page-header">
      <div>
        <p className="eyebrow">多平台 OpenAI 兼容文本模型</p>
        <h1>LLM</h1>
        <p>OneAPI 默认连接本机；也可选择常见云平台或填写自己的 Base URL 与模型 ID。</p>
      </div>
    </header>
    {message && <div className="notice">{message}</div>}
    <section className="split">
      <div className="panel form-panel">
        <h2>{editingId === null ? "添加 LLM 模型入口" : "编辑 LLM 模型入口"}</h2>
        <label>平台
          <select value={provider} onChange={event => chooseProvider(event.target.value)}>
            {providerPresets.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        <label>模型 ID
          <input value={name} onChange={event => setName(event.target.value)} placeholder="例如 gpt-5-mini / deepseek-chat" />
        </label>
        <label>Base URL
          <input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://provider.example/v1" />
        </label>
        <fieldset>
          <legend>密钥来源</legend>
          <div className="segmented">
            <button className={authMode === "environment" ? "selected" : ""} onClick={() => setAuthMode("environment")}>环境变量</button>
            <button className={authMode === "direct" ? "selected" : ""} onClick={() => setAuthMode("direct")}>直接填写</button>
            <button className={authMode === "none" ? "selected" : ""} onClick={() => setAuthMode("none")}>无需密钥</button>
          </div>
        </fieldset>
        {authMode === "environment" && <label>环境变量名称
          <input value={secretEnvName} onChange={event => setSecretEnvName(event.target.value)} placeholder="OPENAI_API_KEY" />
        </label>}
        {authMode === "direct" && <label>API Key
          <input type="password" autoComplete="new-password" value={secretValue} onChange={event => setSecretValue(event.target.value)} placeholder={editingId === null ? "输入密钥" : "留空则保留现有密钥"} />
          <small>直接填写的密钥仅以 Windows 当前用户 DPAPI 加密后保存，API 不会回显。</small>
        </label>}
        <label className="check"><input type="checkbox" checked={vision} onChange={event => setVision(event.target.checked)} />模型也支持视觉输入</label>
        <label>备注<textarea rows={3} value={notes} onChange={event => setNotes(event.target.value)} /></label>
        <div className="actions">
          <button className="primary" disabled={!name.trim() || !baseUrl.trim() || (authMode === "environment" && !secretEnvName.trim()) || directSecretMissing} onClick={() => void save()}>{editingId === null ? "添加模型" : "保存修改"}</button>
          {editingId !== null && <button onClick={reset}>取消编辑</button>}
        </div>
      </div>
      <div className="panel">
        <h2>已配置 LLM</h2>
        <p>连接测试会对每个模型发送一次只要求回复“OK”的最小实调用，并消耗极少量 Token。</p>
        <div className="actions">
          <button disabled={checkingAll || textModels.length === 0} onClick={() => void checkAllModels()}>{checkingAll ? "正在逐个测试…" : "逐个测试全部 LLM"}</button>
        </div>
        {textModels.length === 0
          ? <div className="state-card">尚未添加 LLM 模型。</div>
          : <div className="card-list">{textModels.map(model => <article className={!model.enabled ? "disabled-card" : ""} key={model.id}>
            <div>
              <strong>{model.name}</strong>
              <small>{providerPresets.find(item => item.id === model.provider)?.label ?? model.provider ?? "自定义"} · {model.base_url || "未配置 Base URL"}</small>
              <small>密钥：{model.secret_source === "ENVIRONMENT" ? `${model.secret_env_name} (${model.secret_configured ? "已读取" : "未检测到"})` : model.secret_source === "DIRECT" ? `直接保存 (${model.secret_configured ? "可读取" : "不可读取"})` : "不需要"}</small>
              <small>连接：{modelHealthLabel(healthResults[model.id]?.status ?? "UNKNOWN")}{healthResults[model.id] ? ` · ${healthResults[model.id].detail} · ${healthResults[model.id].latency_ms} ms` : ""}</small>
              <small>{model.notes || "无备注"}</small>
              <div className="tags">{model.capabilities.map(tag => <span key={tag}>{tag}</span>)}</div>
            </div>
            <div className="actions">
              <button disabled={checkingAll || checkingIds.has(model.id)} onClick={() => void checkModel(model)}>{checkingIds.has(model.id) ? "测试中…" : "测试连通性"}</button>
              <button onClick={() => edit(model)}>编辑</button>
              <button onClick={() => void toggleEnabled(model)}>{model.enabled ? "停用" : "启用"}</button>
              <button onClick={() => void remove(model.id)}>删除</button>
            </div>
          </article>)}</div>}
      </div>
    </section>
  </>;
}
