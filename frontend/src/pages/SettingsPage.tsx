import { useEffect, useState } from "react";

import { API_BASE, api } from "../api/client";
import type { AppSettings, SystemStatus } from "../types";

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refreshSystem = async () => {
    try {
      setSystem(await api.systemStatus());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "系统检查失败");
    }
  };
  useEffect(() => {
    api.settings().then(setSettings).catch(error => setMessage(error.message));
    void refreshSystem();
  }, []);
  if (!settings) {
    return <div className="state-card">{message ?? "正在加载设置…"}</div>;
  }

  const update = (
    key: keyof AppSettings,
    value: string | boolean | number,
  ) => setSettings(current => current ? { ...current, [key]: value } : current);
  const save = async () => {
    try {
      setSettings(await api.updateSettings(settings));
      setMessage("设置已保存；新任务将冻结这些配置。");
      await refreshSystem();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "保存失败");
    }
  };
  const shutdown = async () => {
    if (!window.confirm("退出 Video2Doc？正在运行的任务会保存为可恢复状态。")) return;
    try {
      await api.shutdownApp();
      setMessage("Video2Doc 正在安全退出，可以关闭此页面。");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "退出失败");
    }
  };

  return <>
    <header className="page-header">
      <div>
        <p className="eyebrow">Windows 本地应用</p>
        <h1>应用设置</h1>
        <p>密钥只从环境变量读取，不写入界面响应、数据库或任务快照。</p>
      </div>
      <button onClick={() => void refreshSystem()}>重新检查环境</button>
    </header>

    <section className="panel">
      <div className="panel-heading"><h2>运行环境</h2><code>{API_BASE}</code></div>
      {system ? <>
        <div className="tool-grid">
          {Object.entries(system.tools).map(([name, tool]) => (
            <article key={name} className={tool.available ? "tool-ok" : "tool-missing"}>
              <strong>{name}</strong>
              <span>{tool.available ? "可用" : "缺失"}</span>
              <small>{tool.version ?? tool.error ?? tool.configured}</small>
            </article>
          ))}
        </div>
        <dl>
          <div><dt>后端端口</dt><dd>{system.port}</dd></div>
          <div><dt>任务工作区</dt><dd className="break">{system.data_directory}</dd></div>
          <div><dt>日志目录</dt><dd className="break">{system.log_directory}</dd></div>
        </dl>
      </> : <div className="state-card">正在检查媒体工具…</div>}
    </section>

    <section className="panel form-panel settings-form">
      <h2>Obsidian 导出</h2>
      <label>Obsidian Vault 绝对路径
        <input
          value={settings.obsidian_vault_path ?? ""}
          placeholder="D:\\Notes\\MyVault"
          onChange={event => update("obsidian_vault_path", event.target.value)}
        />
      </label>
      <label>默认 Inbox 相对路径
        <input
          value={settings.inbox_path ?? ""}
          placeholder="Inbox"
          onChange={event => update("inbox_path", event.target.value)}
        />
      </label>
      <div className="form-grid">
        <label>文件名模板
          <input
            value={settings.export_filename_template}
            onChange={event => update("export_filename_template", event.target.value)}
          />
        </label>
        <label>同名冲突策略
          <select
            value={settings.export_conflict_policy}
            onChange={event => update(
              "export_conflict_policy",
              event.target.value as AppSettings["export_conflict_policy"],
            )}
          >
            <option value="rename">自动安全改名</option>
            <option value="fail">停止并提示</option>
            <option value="overwrite">明确覆盖（带回滚）</option>
          </select>
        </label>
        <label>附件目录模板
          <input
            value={settings.export_attachment_directory_template}
            onChange={event => update(
              "export_attachment_directory_template",
              event.target.value,
            )}
          />
        </label>
        <label>追溯目录模板
          <input
            value={settings.export_data_directory_template}
            onChange={event => update("export_data_directory_template", event.target.value)}
          />
        </label>
      </div>
      <div className="tag-options">
        <label className="check">
          <input
            type="checkbox"
            checked={settings.export_keep_json_sidecar}
            onChange={event => update("export_keep_json_sidecar", event.target.checked)}
          />
          保留 JSON sidecar
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.export_copy_transcript}
            onChange={event => update("export_copy_transcript", event.target.checked)}
          />
          复制原始转写
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.export_copy_subtitles}
            onChange={event => update("export_copy_subtitles", event.target.checked)}
          />
          有字幕产物时复制字幕
        </label>
      </div>
    </section>

    <section className="panel form-panel settings-form">
      <h2>忠实文本整理</h2>
      <div className="notice">
        OneAPI API Key：{settings.api_key_configured ? "已配置" : "未配置"}；
        Base URL：{settings.oneapi_base_url?.trim() ? "已配置" : "未配置"}。
        {!settings.api_key_configured || !settings.oneapi_base_url?.trim()
          ? " 两项都配置后才能启用文本整理；关闭整理时不影响完整转换。"
          : " 文本整理连接信息完整。"}
      </div>
      <label>OneAPI Base URL
        <input
          value={settings.oneapi_base_url ?? ""}
          placeholder="https://oneapi.example/v1"
          onChange={event => update("oneapi_base_url", event.target.value)}
        />
      </label>
      <div className="form-grid">
        <label>默认文本模型名称
          <input
            value={settings.default_text_model_name ?? ""}
            onChange={event => update("default_text_model_name", event.target.value)}
          />
        </label>
        <label>提示词版本
          <input
            value={settings.text_prompt_version}
            onChange={event => update("text_prompt_version", event.target.value)}
          />
        </label>
        <label>最小文本长度比例
          <input
            type="number"
            min={0.5}
            max={1}
            step={0.01}
            value={settings.faithfulness_min_length_ratio}
            onChange={event => update(
              "faithfulness_min_length_ratio",
              Number(event.target.value),
            )}
          />
        </label>
        <label>公式符号保留比例
          <input
            type="number"
            min={0.5}
            max={1}
            step={0.01}
            value={settings.faithfulness_formula_symbol_ratio}
            onChange={event => update(
              "faithfulness_formula_symbol_ratio",
              Number(event.target.value),
            )}
          />
        </label>
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={settings.default_text_edit_enabled}
          onChange={event => update("default_text_edit_enabled", event.target.checked)}
        />
        新任务默认启用受约束轻度整理
      </label>
    </section>

    <section className="panel form-panel settings-form">
      <h2>处理与本地路径</h2>
      <label>任务资料目录
        <input
          value={settings.data_path ?? ""}
          onChange={event => update("data_path", event.target.value)}
        />
      </label>
      <div className="form-grid">
        <label>Markdown 模板版本
          <input
            value={settings.markdown_template_version}
            onChange={event => update("markdown_template_version", event.target.value)}
          />
        </label>
        <label>图片最小插入间隔（秒）
          <input
            type="number"
            min={0}
            max={3600}
            value={settings.image_min_gap_seconds}
            onChange={event => update("image_min_gap_seconds", Number(event.target.value))}
          />
        </label>
      </div>
      <details>
        <summary>外部媒体工具路径</summary>
        <label>FFmpeg 可执行文件
          <input
            value={settings.ffmpeg_path ?? ""}
            placeholder="ffmpeg 或 C:\\Tools\\ffmpeg.exe"
            onChange={event => update("ffmpeg_path", event.target.value)}
          />
        </label>
        <label>yt-dlp 可执行文件
          <input
            value={settings.ytdlp_path ?? ""}
            placeholder="yt-dlp 或 C:\\Tools\\yt-dlp.exe"
            onChange={event => update("ytdlp_path", event.target.value)}
          />
        </label>
      </details>
      <div className="tag-options">
        <label className="check">
          <input
            type="checkbox"
            checked={settings.default_ocr_enabled}
            onChange={event => update("default_ocr_enabled", event.target.checked)}
          />
          新任务默认启用 OCR
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.default_keep_timestamps}
            onChange={event => update("default_keep_timestamps", event.target.checked)}
          />
          保留时间戳
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={settings.default_keep_raw_transcript}
            onChange={event => update("default_keep_raw_transcript", event.target.checked)}
          />
          保留原始转写
        </label>
      </div>
      <div className="notice">
        Gateway 生命周期以各 Gateway 的 managed_lifecycle 标记为准；应用不会停止用户独立管理的 Docker OCR 容器。
      </div>
      {message && <p className="form-message">{message}</p>}
      <div className="actions">
        <button className="primary" onClick={() => void save()}>保存设置</button>
        <button onClick={() => void shutdown()}>退出 Windows 应用</button>
      </div>
    </section>
  </>;
}
