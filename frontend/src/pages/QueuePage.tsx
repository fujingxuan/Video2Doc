import { useState } from "react";

import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import type { Task } from "../types";
import { formatDate } from "../utils/status";

export function QueuePage({ tasks, refresh }: { tasks: Task[]; refresh: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const act = async (task: Task, action: "cancel" | "retry") => {
    setError(null);
    try { action === "cancel" ? await api.cancelTask(task.id) : await api.retryTask(task.id); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
  };
  return <><header className="page-header"><div><p className="eyebrow">最大并发数 1</p><h1>任务队列</h1><p>任务按创建顺序处理，刷新页面不会丢失状态。</p></div><button onClick={() => void refresh()}>刷新</button></header>{error && <div className="state-card error">{error}</div>}
    <section className="panel">{tasks.length === 0 ? <div className="state-card">队列为空。</div> : <div className="table-wrap"><table><thead><tr><th>任务</th><th>状态</th><th>阶段</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{tasks.map(task => <tr key={task.id}><td><strong>{task.title}</strong><small>{task.source_type}</small></td><td><StatusBadge status={task.status}/></td><td>{task.current_stage}</td><td><div className="progress"><span style={{ width: `${task.progress}%` }}/></div><small>{task.progress}%</small></td><td>{formatDate(task.created_at)}</td><td className="actions"><button onClick={() => { window.location.hash = `/tasks/${task.id}`; }}>详情</button>{["QUEUED", "PROCESSING"].includes(task.status) && <button onClick={() => void act(task, "cancel")}>取消</button>}{["FAILED", "INTERRUPTED"].includes(task.status) && <button onClick={() => void act(task, "retry")}>重试</button>}</td></tr>)}</tbody></table></div>}</section></>;
}
