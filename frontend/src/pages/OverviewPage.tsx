import type { QueueState, Task } from "../types";
import { Icon, type IconName } from "../components/Icon";
import { StatusBadge } from "../components/StatusBadge";
import { formatDate } from "../utils/status";

export function OverviewPage({ queue, tasks, connected }: { queue: QueueState | null; tasks: Task[]; connected: boolean }) {
  const cards: { label: string; value: number; icon: IconName; tone: string }[] = [
    { label: "处理中", value: queue?.running ?? 0, icon: "audio", tone: "teal" },
    { label: "等待中", value: queue?.queued ?? 0, icon: "clock", tone: "blue" },
    { label: "待人工校对", value: queue?.waiting_review ?? 0, icon: "edit", tone: "amber" },
    { label: "已完成", value: queue?.completed ?? 0, icon: "check", tone: "green" },
    { label: "需要处理", value: (queue?.failed ?? 0) + (queue?.interrupted ?? 0), icon: "alert", tone: "rose" },
  ];
  return <>
    <header className="page-header"><div><p className="eyebrow">YOUR WORKSPACE</p><h1>任务总览</h1><p>从一段音视频，开始积累可长久保存的学习材料。</p></div><span className={`connection ${connected ? "online" : "offline"}`}>{connected ? "实时连接" : "连接中断"}</span></header>
    <section className="welcome-panel">
      <div className="welcome-copy"><span className="welcome-kicker">VIDEO → DOCUMENT</span><h2>让每一段内容，<br />都有清晰的文字归处。</h2><p>完整转写、保留细节，将课程与讲座转换为<br className="desktop-break" />可阅读、可追溯的 Obsidian Markdown。</p><button className="welcome-button" onClick={() => { window.location.hash = "/new"; }}><Icon name="plus" width="18" height="18" />开始新的转换<Icon name="arrow" width="18" height="18" /></button></div>
      <div className="conversion-art" aria-hidden="true"><div className="art-orbit orbit-one" /><div className="art-orbit orbit-two" /><div className="media-tile"><div className="media-screen"><Icon name="play" width="43" height="43" /></div><div className="waveform">{[10, 20, 32, 17, 38, 25, 14, 29, 42, 22, 12, 26, 34, 18, 10].map((height, i) => <i key={i} style={{ height }} />)}</div><span>视频与音频</span></div><div className="art-arrow"><Icon name="arrow" width="28" height="28" /></div><div className="document-tile"><div className="document-top"><Icon name="file" width="24" height="24" /><span>Markdown</span></div><div className="document-heading" /><div className="document-line" /><div className="document-line" /><div className="document-line short" /><div className="document-heading second" /><div className="document-line" /><div className="document-line short" /><div className="document-ready"><Icon name="check" width="13" height="13" />完整 · 可追溯</div></div></div>
    </section>
    <section className="metric-grid" aria-label="任务状态">{cards.map(({ label, value, icon, tone }) => <article className="metric" key={label}><div className="metric-heading"><span>{label}</span><span className={`metric-icon tone-${tone}`}><Icon name={icon} width="18" height="18" /></span></div><strong>{value.toString().padStart(2, "0")}</strong><span className="metric-caption">{label === "已完成" ? "已生成学习材料" : label === "需要处理" ? "失败或中断的任务" : label === "待人工校对" ? "等待确认转写内容" : label === "等待中" ? "等待进入处理流程" : "当前正在转换"}</span></article>)}</section>
    <section className="panel recent-panel"><div className="panel-heading"><div><h2>最近任务</h2><p>在这里继续你的内容整理</p></div><button className="text-button" onClick={() => { window.location.hash = "/queue"; }}>查看全部<Icon name="arrow" width="16" height="16" /></button></div>
      {tasks.length === 0 ? <div className="empty-state"><span className="empty-icon"><Icon name="file" width="30" height="30" /></span><h3>你的第一份文字材料，从这里开始</h3><p>添加视频链接或本地音视频文件，转换后即可校对与导出。</p><button onClick={() => { window.location.hash = "/new"; }}><Icon name="plus" width="16" height="16" />创建第一个任务</button></div> : <div className="task-list">{tasks.slice(0, 6).map(task => <button className="task-row" key={task.id} onClick={() => { window.location.hash = `/tasks/${task.id}`; }}><span className="task-file-icon"><Icon name="file" /></span><div className="task-row-content"><strong>{task.title}</strong><small>{formatDate(task.created_at)} · {task.current_stage}</small></div><div className="row-status"><StatusBadge status={task.status} /><span className="task-progress-label">{task.progress}%</span><Icon name="arrow" width="17" height="17" /></div></button>)}</div>}
    </section>
    <div className="workflow-strip"><span><Icon name="play" />导入音视频</span><Icon name="arrow" width="15" height="15" /><span><Icon name="audio" />完整转写</span><Icon name="arrow" width="15" height="15" /><span><Icon name="edit" />校对与整理</span><Icon name="arrow" width="15" height="15" /><span><Icon name="file" />Markdown 导出</span></div>
  </>;
}
