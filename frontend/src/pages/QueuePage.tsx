import { useMemo, useState } from "react";

import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import type { Task } from "../types";
import { formatDate } from "../utils/status";

export function QueuePage({ tasks, refresh }: { tasks: Task[]; refresh: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [clearing, setClearing] = useState(false);
  const terminalTasks = useMemo(
    () => tasks.filter(task => ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(task.status)),
    [tasks],
  );
  const act = async (task: Task, action: "cancel" | "retry") => {
    setError(null);
    try { action === "cancel" ? await api.cancelTask(task.id) : await api.retryTask(task.id); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
  };
  const clearCache = async (allTasks: boolean) => {
    const ids = [...selected].filter(id => terminalTasks.some(task => task.id === id));
    if (!allTasks && ids.length === 0) {
      setError("请先选择需要清理缓存的已结束任务。");
      return;
    }
    const scope = allTasks ? "全部已结束任务" : `${ids.length} 个所选任务`;
    if (!window.confirm(`清除${scope}的可再生缓存？最终 Markdown 和导出文件不会删除。`)) return;
    setClearing(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.clearTaskCache(
        allTasks ? { all_tasks: true } : { task_ids: ids },
      );
      setMessage(
        `已清理 ${result.tasks_cleared} 个任务、${result.files_removed} 个文件，释放 ${formatBytes(result.bytes_freed)}。`
        + (result.skipped.length ? ` 跳过 ${result.skipped.length} 个任务。` : ""),
      );
      setSelected(new Set());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "缓存清理失败");
    } finally {
      setClearing(false);
    }
  };
  const allSelected = terminalTasks.length > 0 && terminalTasks.every(task => selected.has(task.id));
  return <><header className="page-header"><div><p className="eyebrow">最大并发数 1</p><h1>任务队列</h1><p>任务按创建顺序处理，刷新页面不会丢失状态。</p></div><button onClick={() => void refresh()}>刷新</button></header>{error && <div className="state-card error">{error}</div>}{message && <div className="state-card">{message}</div>}
    <section className="panel"><div className="panel-heading"><div className="actions"><button disabled={clearing || selected.size === 0} onClick={() => void clearCache(false)}>清除所选任务缓存</button><button disabled={clearing || terminalTasks.length === 0} onClick={() => void clearCache(true)}>清除全部任务缓存</button></div><small>只清理可重新生成的下载媒体、标准化音频、分块、候选帧和临时日志。</small></div>{tasks.length === 0 ? <div className="state-card">队列为空。</div> : <div className="table-wrap"><table><thead><tr><th><input type="checkbox" aria-label="选择全部已结束任务" checked={allSelected} disabled={terminalTasks.length === 0} onChange={event => setSelected(event.target.checked ? new Set(terminalTasks.map(task => task.id)) : new Set())} /></th><th>任务</th><th>状态</th><th>阶段</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{tasks.map(task => { const terminal = terminalTasks.some(item => item.id === task.id); return <tr key={task.id}><td><input type="checkbox" aria-label={`选择任务 ${task.id}`} disabled={!terminal} checked={selected.has(task.id)} onChange={event => setSelected(current => { const next = new Set(current); if (event.target.checked) next.add(task.id); else next.delete(task.id); return next; })} /></td><td><strong>{task.title}</strong><small>{task.source_type}</small></td><td><StatusBadge status={task.status}/></td><td>{task.current_stage}</td><td><div className="progress"><span style={{ width: `${task.progress}%` }}/></div><small>{task.progress}%</small></td><td>{formatDate(task.created_at)}</td><td className="actions"><button onClick={() => { window.location.hash = `/tasks/${task.id}`; }}>详情</button>{["QUEUED", "PROCESSING"].includes(task.status) && <button onClick={() => void act(task, "cancel")}>取消</button>}{["FAILED", "INTERRUPTED"].includes(task.status) && <button onClick={() => void act(task, "retry")}>重试</button>}</td></tr>; })}</tbody></table></div>}</section></>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}
