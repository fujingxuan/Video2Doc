import { useMemo, useState } from "react";

import { api } from "../api/client";
import type { ModelConfig, ProcessingProfile } from "../types";

export function NewTaskPage({ profiles, models, onCreated }: { profiles: ProcessingProfile[]; models: ModelConfig[]; onCreated: () => Promise<void> }) {
  const [sourceType, setSourceType] = useState<"URL" | "LOCAL_FILE">("URL");
  const [sources, setSources] = useState("");
  const [profileId, setProfileId] = useState("");
  const [asrModelId, setAsrModelId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const asrModels = useMemo(() => models.filter(item => item.enabled && item.capabilities.includes("ASR")), [models]);

  const submit = async () => {
    const inputs = sources.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    if (!inputs.length) { setMessage("请至少输入一个 URL 或本地媒体绝对路径。"); return; }
    if (sourceType === "URL" && inputs.some(value => !/^https?:\/\//i.test(value))) { setMessage("URL 输入必须使用 http 或 https。"); return; }
    if (sourceType === "LOCAL_FILE" && inputs.some(value => !/^(?:[a-z]:[\\/]|\\\\)/i.test(value))) { setMessage("本地媒体必须填写 Windows 绝对路径。"); return; }
    setBusy(true); setMessage(null);
    try {
      const tasks = await api.createTasks({
        pipeline_mode: "REAL",
        inputs: inputs.map(source => ({ source, source_type: sourceType })),
        profile_id: profileId ? Number(profileId) : null,
        asr_model_id: asrModelId ? Number(asrModelId) : null,
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
      <div className="form-grid"><label>处理方案<select value={profileId} onChange={event => setProfileId(event.target.value)}><option value="">默认 / 上次使用</option>{profiles.filter(item => item.enabled).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>ASR 模型配置<select value={asrModelId} onChange={event => setAsrModelId(event.target.value)}><option value="">使用处理方案中的 ASR 模型</option>{asrModels.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div>
      <div className="notice">将执行输入验证、媒体获取、ffprobe 探测、FFmpeg 音频标准化、长音频分块和真实 ASR。OCR 与文本整理不在本阶段执行。</div>
      {message && <p className="form-message">{message}</p>}<div className="actions"><button className="primary" disabled={busy} onClick={() => void submit()}>{busy ? "正在创建…" : "加入串行队列"}</button><button disabled={!sources} onClick={() => setSources("")}>清空输入</button></div>
    </section></>;
}
