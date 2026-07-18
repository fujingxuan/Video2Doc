import { useEffect, useState } from "react";

import { api } from "../api/client";
import { StateView } from "../components/StateView";
import { StatusBadge } from "../components/StatusBadge";
import type { TaskDetail } from "../types";
import { formatDate } from "../utils/status";

export function TaskDetailPage({ taskId }: { taskId: number }) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = async () => { try { setTask(await api.task(taskId)); setError(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "加载失败"); } };
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 1500); return () => window.clearInterval(timer); }, [taskId]);
  const technicalError = task?.stages.find(stage => stage.technical_error)?.technical_error;

  return <StateView loading={!task && !error} error={error}>{task && <>
    <header className="page-header"><div><p className="eyebrow">任务 #{task.id} · 第 {task.attempt} 次执行</p><h1>{task.title}</h1><p className="break">{task.source}</p></div><StatusBadge status={task.status}/></header>
    <section className="detail-grid"><article className="panel"><h2>执行信息</h2><dl><div><dt>输入类型</dt><dd>{task.source_type}</dd></div><div><dt>当前阶段</dt><dd>{task.current_stage}</dd></div><div><dt>总进度</dt><dd>{task.progress}%</dd></div><div><dt>开始时间</dt><dd>{formatDate(task.started_at)}</dd></div><div><dt>工作区</dt><dd className="break">{task.workspace_path ?? "—"}</dd></div><div><dt>结果文件</dt><dd className="break">{task.output_path ?? "—"}</dd></div></dl>{task.error_message && <div className="state-card error"><strong>{task.error_code}</strong><p>{task.error_message}</p>{technicalError && <details><summary>技术详情</summary><pre>{technicalError}</pre></details>}</div>}</article>
      <article className="panel"><h2>媒体信息</h2>{task.media_info ? <dl><div><dt>容器</dt><dd>{task.media_info.container ?? "未知"}</dd></div><div><dt>时长</dt><dd>{task.media_info.duration != null ? `${task.media_info.duration.toFixed(2)} 秒` : "未知"}</dd></div><div><dt>音频</dt><dd>{task.media_info.has_audio ? `有（${task.media_info.audio_streams?.length ?? 0} 流）` : "无"}</dd></div><div><dt>视频</dt><dd>{task.media_info.has_video ? `有（${task.media_info.video_streams?.length ?? 0} 流）` : "无"}</dd></div></dl> : <div className="state-card">媒体尚未完成探测。</div>}</article></section>
    <section className="panel"><h2>转写预览</h2>{task.transcript_preview ? <><div className="preview-meta"><span>Provider：{task.transcript_preview.provider}</span><span>模型：{task.transcript_preview.model}</span><span>分块：{task.transcript_preview.completed_chunks}/{task.transcript_preview.chunk_count}</span><span>分段：{task.transcript_preview.segment_count}</span></div><pre className="transcript-preview">{task.transcript_preview.text}</pre>{task.transcript_preview.truncated && <small>预览已截断；完整结果保存在工作区。</small>}</> : <div className="state-card">ASR 结果尚未生成。</div>}</section>
    <section className="panel"><h2>Pipeline 阶段</h2>{task.stages.length === 0 ? <div className="state-card">尚未开始。</div> : <div className="timeline">{task.stages.map(stage => <div key={stage.id}><span className={`dot ${stage.status.toLowerCase()}`}/><div><strong>{stage.stage}</strong><small>第 {stage.attempt} 次 · {stage.status} · 阶段进度 {stage.progress}%</small>{stage.message && <p>{stage.message}</p>}{stage.log_path && <small className="break">日志：{stage.log_path}</small>}</div></div>)}</div>}</section>
    <section className="detail-grid"><article className="panel"><h2>工作区产物（{task.artifacts.length}）</h2><div className="artifact-list">{task.artifacts.map(item => <div key={item.path}><code>{item.path}</code><small>{item.size.toLocaleString()} B</small></div>)}</div></article><article className="panel"><h2>配置快照</h2><pre>{JSON.stringify(task.snapshot.snapshot_json, null, 2)}</pre></article></section>
  </>}</StateView>;
}
