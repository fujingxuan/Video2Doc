import { useState } from "react";

import { api } from "../api/client";
import type { ProcessingProfile } from "../types";

export function NewTaskPage({ profiles, onCreated }: { profiles: ProcessingProfile[]; onCreated: () => Promise<void> }) {
  const [sourceType, setSourceType] = useState<"URL" | "LOCAL_FILE">("URL");
  const [sources, setSources] = useState("");
  const [profileId, setProfileId] = useState("");
  const [ocrEnabled, setOcrEnabled] = useState(false);
  const [failureMode, setFailureMode] = useState<"never" | "once" | "always">("never");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const addFiles = (files: FileList | null) => {
    if (!files?.length) return;
    const names = Array.from(files, file => file.name);
    setSourceType("LOCAL_FILE");
    setSources(current => [current.trim(), ...names].filter(Boolean).join("\n"));
  };

  const submit = async () => {
    const inputs = sources.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    if (!inputs.length) { setMessage("请至少输入一个 URL 或本地文件路径。"); return; }
    setBusy(true); setMessage(null);
    try {
      const tasks = await api.createTasks({
        inputs: inputs.map(source => ({ source, source_type: sourceType, simulate_failure: failureMode })),
        profile_id: profileId ? Number(profileId) : null,
        config_overrides: { ocr_enabled: ocrEnabled },
      });
      setSources("");
      setMessage(`已创建 ${tasks.length} 个模拟任务。`);
      await onCreated();
    } catch (error) { setMessage(error instanceof Error ? error.message : "创建失败"); }
    finally { setBusy(false); }
  };

  return <><header className="page-header"><div><p className="eyebrow">模拟输入</p><h1>新建任务</h1><p>每行一个输入；系统会为每项创建独立配置快照。</p></div></header>
    <section className="panel form-panel">
      <div className="segmented"><button className={sourceType === "URL" ? "selected" : ""} onClick={() => setSourceType("URL")}>URL</button><button className={sourceType === "LOCAL_FILE" ? "selected" : ""} onClick={() => setSourceType("LOCAL_FILE")}>本地文件路径</button></div>
      <label>输入列表<textarea rows={7} value={sources} onChange={event => setSources(event.target.value)} placeholder={sourceType === "URL" ? "https://example.com/video\nhttps://example.com/lecture" : "D:\\Media\\lecture.mp4\nD:\\Media\\audio.m4a"}/></label>
      {sourceType === "LOCAL_FILE" && <div className="drop-zone" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); addFiles(event.dataTransfer.files); }}><strong>拖放本地媒体，或选择多个文件</strong><small>第一轮只把文件名加入模拟队列，不读取或上传文件内容。</small><input type="file" multiple accept="video/*,audio/*,.mkv,.m4a" onChange={event => addFiles(event.target.files)}/></div>}
      <div className="form-grid"><label>处理方案<select value={profileId} onChange={event => setProfileId(event.target.value)}><option value="">默认 / 上次使用</option>{profiles.filter(item => item.enabled).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>失败模拟（仅测试）<select value={failureMode} onChange={event => setFailureMode(event.target.value as typeof failureMode)}><option value="never">不失败</option><option value="once">第一次失败</option><option value="always">始终失败</option></select></label></div>
      <label className="check"><input type="checkbox" checked={ocrEnabled} onChange={event => setOcrEnabled(event.target.checked)}/>在配置快照中启用 OCR 模拟阶段</label>
      <div className="notice">所有步骤均为模拟计时和状态更新，不会读取输入、下载媒体或调用模型。</div>
      {message && <p className="form-message">{message}</p>}<div className="actions"><button className="primary" disabled={busy} onClick={() => void submit()}>{busy ? "正在创建…" : "加入串行队列"}</button><button disabled={!sources} onClick={() => setSources("")}>清空输入</button></div>
    </section></>;
}
