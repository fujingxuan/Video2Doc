import type { QueueState, Task } from "../types";
import { StatusBadge } from "../components/StatusBadge";
import { formatDate } from "../utils/status";

export function OverviewPage({ queue, tasks, connected }: { queue: QueueState | null; tasks: Task[]; connected: boolean }) {
  const cards = [
    ["处理中", queue?.running ?? 0], ["等待中", queue?.queued ?? 0], ["待人工校对", queue?.waiting_review ?? 0], ["已完成", queue?.completed ?? 0], ["需要处理", (queue?.failed ?? 0) + (queue?.interrupted ?? 0)],
  ];
  return <>
    <header className="page-header"><div><p className="eyebrow">工作台</p><h1>任务总览</h1><p>查看完整转换任务和单并发持久化队列状态。</p></div><span className={`connection ${connected ? "online" : "offline"}`}>{connected ? "实时连接" : "连接中断"}</span></header>
    <section className="metric-grid">{cards.map(([label, value]) => <article className="metric" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
    <section className="panel"><div className="panel-heading"><h2>最近任务</h2><button onClick={() => { window.location.hash = "/new"; }}>新建转换任务</button></div>
      {tasks.length === 0 ? <div className="state-card">尚未创建任务。</div> : <div className="task-list">{tasks.slice(0, 6).map(task => <button className="task-row" key={task.id} onClick={() => { window.location.hash = `/tasks/${task.id}`; }}><div><strong>{task.title}</strong><small>{formatDate(task.created_at)} · {task.current_stage}</small></div><div className="row-status"><StatusBadge status={task.status}/><span>{task.progress}%</span></div></button>)}</div>}
    </section>
  </>;
}
