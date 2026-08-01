import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { StateView } from "../components/StateView";
import { StatusBadge } from "../components/StatusBadge";
import type { TaskDetail } from "../types";
import { formatDate, formatDuration } from "../utils/status";

export function TaskDetailPage({ taskId }: { taskId: number }) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const load = async () => {
    try {
      setTask(await api.task(taskId));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    }
  };
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [taskId]);

  const attempts = useMemo(() => {
    const grouped = new Map<number, TaskDetail["stages"]>();
    for (const stage of task?.stages ?? []) {
      grouped.set(stage.attempt, [...(grouped.get(stage.attempt) ?? []), stage]);
    }
    return [...grouped.entries()].sort(([left], [right]) => right - left);
  }, [task?.stages]);
  const frameArtifacts = (task?.artifacts ?? []).filter(item =>
    /01_extract\/frames\/normalized\/.+\.(?:jpe?g|png|webp)$/i.test(item.path),
  ).slice(0, 12);
  const technicalError = task?.stages.find(stage => stage.technical_error)?.technical_error;

  const operate = async (
    action: "cancel" | "retry" | "workspace" | "export_directory" | "markdown",
  ) => {
    if (!task) return;
    setActionMessage(null);
    try {
      if (action === "cancel") await api.cancelTask(task.id);
      else if (action === "retry") await api.retryTask(task.id);
      else await api.openTaskTarget(task.id, action);
      setActionMessage(action === "retry" ? "任务已重新加入队列。" : "操作已完成。");
      await load();
    } catch (reason) {
      setActionMessage(reason instanceof Error ? reason.message : "操作失败");
    }
  };

  const copyError = async () => {
    if (!task) return;
    const text = [
      task.error_code,
      task.error_message,
      technicalError,
    ].filter(Boolean).join("\n");
    await navigator.clipboard.writeText(text);
    setActionMessage("错误信息已复制。");
  };

  return <StateView loading={!task && !error} error={error}>{task && <>
    <header className="page-header">
      <div>
        <p className="eyebrow">任务 #{task.id} · 第 {task.attempt} 次执行</p>
        <h1>{task.title}</h1>
        <p className="break">{task.source}</p>
      </div>
      <StatusBadge status={task.status} />
    </header>

    <section className="panel action-bar">
      <div className="actions">
        {["QUEUED", "PROCESSING"].includes(task.status) && (
          <button onClick={() => void operate("cancel")}>取消任务</button>
        )}
        {["FAILED", "INTERRUPTED"].includes(task.status) && (
          <button className="primary" onClick={() => void operate("retry")}>重试并复用产物</button>
        )}
        {task.workspace_path && (
          <button onClick={() => void operate("workspace")}>打开任务工作区</button>
        )}
        {task.export_path && <>
          <button onClick={() => void operate("export_directory")}>打开导出目录</button>
          <button onClick={() => void operate("markdown")}>打开最终 Markdown</button>
        </>}
        {task.error_message && <button onClick={() => void copyError()}>复制错误信息</button>}
      </div>
      {actionMessage && <span>{actionMessage}</span>}
    </section>

    <section className="detail-grid">
      <article className="panel">
        <h2>执行信息</h2>
        <dl>
          <div><dt>输入类型</dt><dd>{task.source_type}</dd></div>
          <div><dt>当前阶段</dt><dd>{task.current_stage}</dd></div>
          <div><dt>总进度</dt><dd>{task.progress}%</dd></div>
          <div><dt>开始时间</dt><dd>{formatDate(task.started_at)}</dd></div>
          <div><dt>工作区</dt><dd className="break">{task.workspace_path ?? "—"}</dd></div>
          <div><dt>最终笔记</dt><dd className="break">{task.export_path ?? "—"}</dd></div>
        </dl>
        {task.error_message && <div className="state-card error">
          <strong>{task.error_code}</strong>
          <p>{task.error_message}</p>
          {technicalError && <details><summary>技术详情</summary><pre>{technicalError}</pre></details>}
        </div>}
      </article>
      <article className="panel">
        <h2>媒体信息</h2>
        {task.media_info ? <dl>
          <div><dt>容器</dt><dd>{task.media_info.container ?? "未知"}</dd></div>
          <div><dt>时长</dt><dd>{task.media_info.duration != null ? `${task.media_info.duration.toFixed(2)} 秒` : "未知"}</dd></div>
          <div><dt>音频</dt><dd>{task.media_info.has_audio ? `有（${task.media_info.audio_streams?.length ?? 0} 流）` : "无"}</dd></div>
          <div><dt>视频</dt><dd>{task.media_info.has_video ? `有（${task.media_info.video_streams?.length ?? 0} 流）` : "无"}</dd></div>
        </dl> : <div className="state-card">媒体尚未完成探测。</div>}
      </article>
    </section>

    <section className="panel">
      <h2>文本预览</h2>
      {task.transcript_preview ? <>
        <div className="preview-meta">
          <span>Provider：{task.transcript_preview.provider}</span>
          {task.transcript_preview.model !== "not-applicable" && (
            <span>模型：{task.transcript_preview.model}</span>
          )}
          <span>分块：{task.transcript_preview.completed_chunks}/{task.transcript_preview.chunk_count}</span>
          <span>分段：{task.transcript_preview.segment_count}</span>
        </div>
        <pre className="transcript-preview">{task.transcript_preview.text}</pre>
        {task.transcript_preview.truncated && <small>预览已截断；完整结果保存在工作区。</small>}
      </> : <div className="state-card">ASR 或导入字幕文本尚未生成。</div>}
    </section>

    <section className="detail-grid">
      <article className="panel">
        <h2>视频抽帧与 OCR</h2>
        {task.frame_summary ? <dl>
          <div><dt>候选 / 保留</dt><dd>{task.frame_summary.candidate_count ?? 0} / {task.frame_summary.kept_count ?? 0}</dd></div>
          <div><dt>去重</dt><dd>{task.frame_summary.dropped_count ?? 0}</dd></div>
          <div><dt>OCR 文本块</dt><dd>{task.ocr_preview?.block_count ?? 0}</dd></div>
          <div><dt>重试复用</dt><dd>{task.ocr_preview?.reused_count ?? 0}</dd></div>
        </dl> : <div className="state-card">抽帧尚未完成或 OCR 未启用。</div>}
        {task.ocr_preview?.text && <pre className="ocr-preview">{task.ocr_preview.text}</pre>}
      </article>
      <article className="panel">
        <h2>融合与文档</h2>
        {task.fusion_preview ? <dl>
          <div><dt>融合事件</dt><dd>{task.fusion_preview.event_count ?? 0}</dd></div>
          <div><dt>ASR / OCR / 帧</dt><dd>
            {task.fusion_preview.counts?.asr_segment ?? 0} / {" "}
            {task.fusion_preview.counts?.ocr_event ?? 0} / {" "}
            {task.fusion_preview.counts?.frame_event ?? 0}
          </dd></div>
          <div><dt>文档节 / 块</dt><dd>
            {task.document_preview?.section_count ?? 0} / {task.document_preview?.block_count ?? 0}
          </dd></div>
          <div><dt>文档 schema</dt><dd>v{task.document_preview?.schema_version ?? "—"}</dd></div>
        </dl> : <div className="state-card">融合时间线尚未生成。</div>}
      </article>
    </section>

    {frameArtifacts.length > 0 && <section className="panel">
      <h2>OCR 相关画面</h2>
      <div className="frame-gallery">
        {frameArtifacts.map(item => (
          <a key={item.path} href={api.artifactUrl(task.id, item.path)} target="_blank" rel="noreferrer">
            <img src={api.artifactUrl(task.id, item.path)} alt={item.path} loading="lazy" />
            <small>{item.path.split("/").at(-1)}</small>
          </a>
        ))}
      </div>
    </section>}

    <section className="panel">
      <div className="panel-heading">
        <h2>Markdown 预览</h2>
        <span>{task.export_path ?? "尚未导出"}</span>
      </div>
      {task.markdown_preview
        ? <pre className="markdown-preview">{task.markdown_preview}</pre>
        : <div className="state-card">Markdown 尚未生成。</div>}
      {task.exports.length > 0 && <div className="export-list">
        {task.exports.map(item => <article key={item.id}>
          <strong>{item.target_type} · 第 {item.attempt} 次 · {item.status}</strong>
          <small className="break">{item.markdown_path ?? item.error_message ?? "—"}</small>
          <span>图片 {item.image_count} · sidecar/原始产物 {item.sidecar_count}</span>
        </article>)}
      </div>}
    </section>

    <section className="panel">
      <h2>Attempt 与 Pipeline 阶段</h2>
      {attempts.length === 0 ? <div className="state-card">尚未开始。</div> : attempts.map(
        ([attempt, stages]) => <details key={attempt} open={attempt === task.attempt}>
          <summary>第 {attempt} 次执行 · {stages.length} 个阶段</summary>
          <div className="timeline">{stages.map(stage => <div key={stage.id}>
            <span className={`dot ${stage.status.toLowerCase()}`} />
            <div>
              <strong>{stage.stage}</strong>
              <small>{stage.status} · 阶段进度 {stage.progress}% · 耗时 {formatDuration(stage.started_at, stage.finished_at)}</small>
              {stage.model_usage.length > 0
                ? <div className="tags">{stage.model_usage.map((usage, index) => (
                  <span key={`${usage.capability}-${index}`}>
                    {usage.capability}：{usage.gateway_name ? `${usage.gateway_name} / ` : ""}
                    {usage.display_name}
                  </span>
                ))}</div>
                : <small>模型：未调用（本地工具或确定性处理）</small>}
              {stage.message && <p>{stage.message}</p>}
              {stage.log_path && <small className="break">日志：{stage.log_path}</small>}
            </div>
          </div>)}</div>
        </details>,
      )}
    </section>

    <section className="detail-grid">
      <article className="panel">
        <h2>工作区产物（{task.artifacts.length}）</h2>
        <div className="artifact-list">{task.artifacts.map(item => <div key={item.path}>
          <a href={api.artifactUrl(task.id, item.path)} target="_blank" rel="noreferrer">
            <code>{item.path}</code>
          </a>
          <small>{item.size.toLocaleString()} B</small>
        </div>)}</div>
      </article>
      <article className="panel">
        <h2>不可变配置快照 · schema v{task.snapshot.schema_version}</h2>
        <pre>{JSON.stringify(task.snapshot.snapshot_json, null, 2)}</pre>
      </article>
    </section>
  </>}</StateView>;
}
