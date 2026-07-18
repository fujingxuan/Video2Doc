import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { AppSettings } from "../types";

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { api.settings().then(setSettings).catch(error => setMessage(error.message)); }, []);
  if (!settings) return <div className="state-card">{message ?? "正在加载设置…"}</div>;
  const update = (key: keyof AppSettings, value: string | boolean) => setSettings(current => current ? { ...current, [key]: value } : current);
  const save = async () => { try { setSettings(await api.updateSettings(settings)); setMessage("设置已保存。"); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); } };

  return <><header className="page-header"><div><p className="eyebrow">本地持久化</p><h1>应用设置</h1><p>API Key 只从环境变量读取，界面、日志、数据库和任务快照都不保存明文。</p></div></header>
    <section className="panel form-panel settings-form">
      <div className="notice">OneAPI ASR：{settings.api_key_configured ? "环境变量中已配置密钥" : "未配置；真实 ASR 任务会明确失败"}</div>
      <label>OneAPI Base URL<input value={settings.oneapi_base_url ?? ""} onChange={event => update("oneapi_base_url", event.target.value)}/></label>
      <label>Obsidian Vault 路径<input value={settings.obsidian_vault_path ?? ""} onChange={event => update("obsidian_vault_path", event.target.value)}/></label>
      <label>成品 Inbox 路径<input value={settings.inbox_path ?? ""} onChange={event => update("inbox_path", event.target.value)}/></label>
      <label>任务资料目录<input value={settings.data_path ?? ""} onChange={event => update("data_path", event.target.value)}/></label>
      <details><summary>真实媒体外部工具路径</summary><label>FFmpeg 可执行文件<input value={settings.ffmpeg_path ?? ""} placeholder="ffmpeg 或 C:\\Tools\\ffmpeg.exe" onChange={event => update("ffmpeg_path", event.target.value)}/></label><label>yt-dlp 可执行文件<input value={settings.ytdlp_path ?? ""} placeholder="yt-dlp 或 C:\\Tools\\yt-dlp.exe" onChange={event => update("ytdlp_path", event.target.value)}/></label><label>OCR 命令（本阶段不调用）<input value={settings.ocr_command ?? ""} onChange={event => update("ocr_command", event.target.value)}/></label></details>
      <div className="tag-options"><label className="check"><input type="checkbox" checked={settings.default_ocr_enabled} onChange={event => update("default_ocr_enabled", event.target.checked)}/>默认启用 OCR（后续阶段）</label><label className="check"><input type="checkbox" checked={settings.default_keep_timestamps} onChange={event => update("default_keep_timestamps", event.target.checked)}/>保留时间戳</label><label className="check"><input type="checkbox" checked={settings.default_keep_raw_transcript} onChange={event => update("default_keep_raw_transcript", event.target.checked)}/>保留原始转写</label></div>
      {message && <p className="form-message">{message}</p>}<button className="primary" onClick={() => void save()}>保存设置</button>
    </section></>;
}
