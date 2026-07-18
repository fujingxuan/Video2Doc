import { useEffect, useState } from "react";

import { api } from "../api/client";
import { StateView } from "../components/StateView";
import { StatusBadge } from "../components/StatusBadge";
import type { TaskDetail } from "../types";
import { formatDate } from "../utils/status";

export function TaskDetailPage({ taskId }: { taskId: number }) {
  const [task, setTask] = useState<TaskDetail | null>(null); const [error, setError] = useState<string | null>(null);
  const load = async () => { try { setTask(await api.task(taskId)); setError(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "加载失败"); } };
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 1500); return () => window.clearInterval(timer); }, [taskId]);
  return <StateView loading={!task && !error} error={error}>{task && <><header className="page-header"><div><p className="eyebrow">任务 #{task.id} · 第 {task.attempt} 次执行</p><h1>{task.title}</h1><p className="break">{task.source}</p></div><StatusBadge status={task.status}/></header>
    <section className="detail-grid"><article className="panel"><h2>执行信息</h2><dl><div><dt>当前阶段</dt><dd>{task.current_stage}</dd></div><div><dt>总进度</dt><dd>{task.progress}%</dd></div><div><dt>开始时间</dt><dd>{formatDate(task.started_at)}</dd></div><div><dt>工作区</dt><dd className="break">{task.workspace_path ?? "—"}</dd></div><div><dt>输出文件</dt><dd className="break">{task.output_path ?? "—"}</dd></div></dl>{task.error_message && <div className="state-card error">{task.error_code}: {task.error_message}</div>}</article>
    <article className="panel"><h2>配置快照</h2><pre>{JSON.stringify(task.snapshot.snapshot_json, null, 2)}</pre></article></section>
    <section className="panel"><h2>Pipeline 阶段</h2>{task.stages.length === 0 ? <div className="state-card">尚未开始。</div> : <div className="timeline">{task.stages.map(stage => <div key={stage.id}><span className={`dot ${stage.status.toLowerCase()}`}/><div><strong>{stage.stage}</strong><small>第 {stage.attempt} 次 · {stage.status} · {stage.progress}%</small>{stage.error_message && <p>{stage.error_message}</p>}</div></div>)}</div>}</section></>}</StateView>;
}
