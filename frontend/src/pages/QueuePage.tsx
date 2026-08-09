import { useMemo, useState } from "react";

import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import type { Task } from "../types";
import { formatDate } from "../utils/status";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"]);

export function QueuePage({ tasks, refresh }: { tasks: Task[]; refresh: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [working, setWorking] = useState(false);
  const terminalTasks = useMemo(() => tasks.filter(task => TERMINAL.has(task.status)), [tasks]);
  const deletableTasks = useMemo(
    () => tasks.filter(task => task.status !== "PROCESSING"),
    [tasks],
  );
  const selectedDeletable = useMemo(
    () => deletableTasks.filter(task => selected.has(task.id)),
    [deletableTasks, selected],
  );

  const act = async (task: Task, action: "cancel" | "retry") => {
    setError(null);
    try {
      action === "cancel" ? await api.cancelTask(task.id) : await api.retryTask(task.id);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    }
  };

  const clearCache = async (allTasks: boolean) => {
    const ids = [...selected].filter(id => terminalTasks.some(task => task.id === id));
    if (!allTasks && ids.length === 0) {
      setError("请先选择需要清理缓存的已结束任务。");
      return;
    }
    const scope = allTasks ? "全部已结束任务" : `${ids.length} 个所选任务`;
    if (!window.confirm(`清除${scope}的可再生缓存？最终 Markdown 和导出文件不会删除。`)) return;
    setWorking(true);
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
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "缓存清理失败");
    } finally {
      setWorking(false);
    }
  };

  const deleteTasks = async (targets: Task[]) => {
    if (!targets.length) {
      setError("请先选择需要删除的任务。");
      return;
    }
    const names = targets.slice(0, 3).map(task => `“${task.title}”`).join("、");
    const suffix = targets.length > 3 ? `等 ${targets.length} 个任务` : "";
    if (!window.confirm(
      `确定删除 ${names}${suffix}？\n\n任务记录、工作区、已导出的 Markdown、附件和追溯文件都会删除；原始输入媒体不会删除。此操作不可撤销。`,
    )) return;

    setWorking(true);
    setError(null);
    setMessage(null);
    let deleted = 0;
    let filesRemoved = 0;
    let bytesFreed = 0;
    try {
      for (const task of targets) {
        const result = await api.deleteTask(task.id);
        deleted += 1;
        filesRemoved += result.files_removed;
        bytesFreed += result.bytes_freed;
      }
      setSelected(current => {
        const next = new Set(current);
        targets.forEach(task => next.delete(task.id));
        return next;
      });
      setMessage(
        `已删除 ${deleted} 个任务及 ${filesRemoved} 个对应文件，释放 ${formatBytes(bytesFreed)}。`,
      );
      await refresh();
    } catch (reason) {
      await refresh();
      setError(
        `已删除 ${deleted} 个任务，随后停止：${reason instanceof Error ? reason.message : "删除失败"}`,
      );
    } finally {
      setWorking(false);
    }
  };

  const allSelected = deletableTasks.length > 0
    && deletableTasks.every(task => selected.has(task.id));
  const selectedTerminalCount = terminalTasks.filter(task => selected.has(task.id)).length;

  return <>
    <header className="page-header">
      <div>
        <p className="eyebrow">最大并发数 1</p>
        <h1>任务队列</h1>
        <p>待处理任务可修改完整设置；删除任务时会同时清除它生成的工作区与导出文件。</p>
      </div>
      <button onClick={() => void refresh()}>刷新</button>
    </header>
    {error && <div className="state-card error">{error}</div>}
    {message && <div className="state-card success">{message}</div>}
    <section className="panel queue-panel">
      <div className="panel-heading queue-toolbar">
        <div className="actions">
          <button
            className="danger-button"
            disabled={working || selectedDeletable.length === 0}
            onClick={() => void deleteTasks(selectedDeletable)}
          >
            删除所选任务及文件
          </button>
          <button
            disabled={working || selectedTerminalCount === 0}
            onClick={() => void clearCache(false)}
          >
            清除所选缓存
          </button>
          <button
            disabled={working || terminalTasks.length === 0}
            onClick={() => void clearCache(true)}
          >
            清除全部已结束缓存
          </button>
        </div>
        <small>“清除缓存”保留成品；“删除任务”会删除该任务的全部生成物。运行中的任务需先取消。</small>
      </div>
      {tasks.length === 0 ? <div className="state-card">队列为空。</div> : <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="选择全部可删除任务"
                  checked={allSelected}
                  disabled={deletableTasks.length === 0}
                  onChange={event => setSelected(
                    event.target.checked ? new Set(deletableTasks.map(task => task.id)) : new Set(),
                  )}
                />
              </th>
              <th>任务</th><th>状态</th><th>阶段</th><th>进度</th><th>创建时间</th><th>操作</th>
            </tr>
          </thead>
          <tbody>{tasks.map(task => {
            const deletable = task.status !== "PROCESSING";
            return <tr key={task.id}>
              <td>
                <input
                  type="checkbox"
                  aria-label={`选择任务 ${task.id}`}
                  disabled={!deletable || working}
                  checked={selected.has(task.id)}
                  onChange={event => setSelected(current => {
                    const next = new Set(current);
                    if (event.target.checked) next.add(task.id);
                    else next.delete(task.id);
                    return next;
                  })}
                />
              </td>
              <td><strong>{task.title}</strong><small>{task.source_type}</small></td>
              <td><StatusBadge status={task.status} /></td>
              <td>{task.current_stage}</td>
              <td><div className="progress"><span style={{ width: `${task.progress}%` }} /></div><small>{task.progress}%</small></td>
              <td>{formatDate(task.created_at)}</td>
              <td className="actions queue-actions">
                <button onClick={() => { window.location.hash = `/tasks/${task.id}`; }}>详情</button>
                {task.status === "QUEUED" && <button onClick={() => { window.location.hash = `/tasks/${task.id}/edit`; }}>编辑</button>}
                {["QUEUED", "PROCESSING"].includes(task.status) && <button onClick={() => void act(task, "cancel")}>取消</button>}
                {["FAILED", "INTERRUPTED"].includes(task.status) && <button onClick={() => void act(task, "retry")}>重试</button>}
                {deletable && <button className="danger-button" disabled={working} onClick={() => void deleteTasks([task])}>删除</button>}
              </td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
    </section>
  </>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}
