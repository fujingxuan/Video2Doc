import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useTaskFeed } from "../hooks/useTaskFeed";
import type { Task, TaskStatus } from "../types";
import { formatDate, statusLabel } from "../utils/status";
import { virtualRows } from "../utils/taskFeed";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"]);
const STATUSES: TaskStatus[] = ["QUEUED", "PROCESSING", "WAITING_REVIEW", "COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"];
type Confirmation = { kind: "purge"; ids: number[] } | { kind: "expired" } | { kind: "cache"; all: boolean };

export function QueuePage({ trashed = false }: { trashed?: boolean }) {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(100);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [working, setWorking] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [undoIds, setUndoIds] = useState<number[]>([]);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(600);
  const viewport = useRef<HTMLDivElement>(null);
  const feed = useTaskFeed({ offset, limit, status, search: term, trashed });
  const page = feed.data?.page;
  const tasks = page?.items ?? [];
  const selectable = useMemo(() => tasks.filter(task => !["PROCESSING", "DELETING"].includes(task.status)), [tasks]);
  const targets = selectable.filter(task => selected.has(task.id));
  const selectedTerminal = tasks.filter(task => selected.has(task.id) && TERMINAL.has(task.status));
  const windowRows = virtualRows(tasks.length, Math.max(0, scrollTop - 48), viewportHeight);
  const actualOffset = page?.offset ?? offset;
  const total = page?.total ?? 0;

  useEffect(() => {
    if (search.trim() === term) return;
    const timer = window.setTimeout(() => { setTerm(search.trim()); setOffset(0); }, 300);
    return () => window.clearTimeout(timer);
  }, [search, term]);
  useEffect(() => {
    setSelected(new Set());
    setScrollTop(0);
    if (viewport.current) viewport.current.scrollTop = 0;
  }, [offset, limit, status, term]);
  useEffect(() => {
    if (page && page.offset !== offset) setOffset(page.offset);
  }, [page?.offset, offset]);
  useEffect(() => {
    if (!viewport.current) return;
    const observer = new ResizeObserver(entries => setViewportHeight(entries[0].contentRect.height));
    observer.observe(viewport.current);
    return () => observer.disconnect();
  }, [!!page, tasks.length > 0]);

  const act = async (task: Task, action: "cancel" | "retry") => {
    setWorking(true);
    setError(null);
    try {
      action === "cancel" ? await api.cancelTask(task.id) : await api.retryTask(task.id);
      await feed.refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
    finally { setWorking(false); }
  };

  const move = async (ids: number[], action: "trash" | "restore" | "purge") => {
    if (!ids.length) return;
    setWorking(true);
    setError(null);
    setMessage(null);
    const finished: number[] = [];
    const label = action === "trash" ? "移入回收站" : action === "restore" ? "恢复" : "永久删除";
    try {
      for (const id of ids) {
        if (action === "trash") await api.deleteTask(id);
        else if (action === "restore") await api.restoreTask(id);
        else await api.purgeTask(id);
        finished.push(id);
      }
      setMessage(`已${label} ${finished.length} 个任务。`);
    } catch (reason) {
      setError(`已${label} ${finished.length} 个任务，随后停止：${reason instanceof Error ? reason.message : "操作失败"}`);
    } finally {
      setUndoIds(current => action === "trash" ? finished : current.filter(id => !finished.includes(id)));
      setSelected(current => new Set([...current].filter(id => !finished.includes(id))));
      await feed.refresh();
      setWorking(false);
    }
  };

  const clearCache = async (allTasks: boolean) => {
    if (!allTasks && !selectedTerminal.length) return;
    setWorking(true);
    setError(null);
    try {
      const result = await api.clearTaskCache(allTasks ? { all_tasks: true } : { task_ids: selectedTerminal.map(task => task.id) });
      setMessage(`已清理 ${result.tasks_cleared} 个任务、${result.files_removed} 个文件，释放 ${formatBytes(result.bytes_freed)}。${result.skipped.length ? ` 跳过 ${result.skipped.length} 个任务。` : ""}`);
      await feed.refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "缓存清理失败"); }
    finally { setWorking(false); }
  };

  const purgeExpired = async () => {
    setWorking(true);
    setError(null);
    try {
      const result = await api.purgeExpiredTasks();
      setMessage(`已永久清理 ${result.deleted.length} 个过期任务。${result.skipped.length ? ` 跳过 ${result.skipped.length} 项：${result.skipped[0].reason}` : ""}`);
      await feed.refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "清理失败"); }
    finally { setWorking(false); }
  };

  return <>
    {confirmation && <ConfirmDialog
      title={confirmation.kind === "cache" ? "确认清除缓存" : "确认永久删除"}
      message={confirmation.kind === "purge"
        ? `永久删除这 ${confirmation.ids.length} 个任务？任务记录、工作区、导出的 Markdown、附件和追溯文件都会删除，原始输入媒体保留。此操作不可撤销。`
        : confirmation.kind === "expired"
          ? "永久清理回收站中已满 30 天的任务及其生成文件？每次最多清理 100 项，范围不受当前筛选限制，原始输入媒体保留。此操作不可撤销。"
          : `清除${confirmation.all ? "全部未在回收站中的已结束任务" : `${selectedTerminal.length} 个所选任务`}的可再生缓存？最终 Markdown 和导出文件保留。`}
      confirmLabel={confirmation.kind === "cache" ? "确认清除缓存" : "确认永久删除"}
      onCancel={() => setConfirmation(null)}
      onConfirm={() => {
        setConfirmation(null);
        if (confirmation.kind === "purge") void move(confirmation.ids, "purge");
        else if (confirmation.kind === "expired") void purgeExpired();
        else void clearCache(confirmation.all);
      }}
    />}
    <header className="page-header">
      <div>
        <p className="eyebrow">{trashed ? "可恢复的任务与文件" : "转写 1 个 · 音频预取 1 个"}</p>
        <h1>{trashed ? "回收站" : "任务队列"}</h1>
        <p>{trashed ? "生成文件原位保留；满 30 天后可手动清理过期项。恢复等待中的任务后会继续排队。" : "搜索和筛选历史任务。移入回收站后可撤销或恢复，运行中的任务需先取消。"}</p>
      </div>
      <div className="actions">
        <span className={`connection ${feed.connected ? "online" : ""}`}>{feed.connected ? "实时连接" : "正在同步"}</span>
        <button disabled={working} onClick={() => void feed.refresh()}>刷新</button>
      </div>
    </header>
    {(error || feed.error) && <div className="state-card error" role="alert">{error || feed.error}</div>}
    {(message || undoIds.length > 0) && <div className="state-card success" role="status">
      {message}
      {undoIds.length > 0 && <button disabled={working} onClick={() => void move(undoIds, "restore")}>撤销移入回收站（{undoIds.length}）</button>}
    </div>}
    <section className="panel queue-panel">
      <div className="history-filters">
        <label>搜索任务<input type="search" placeholder="标题或来源" maxLength={200} value={search} disabled={working} onChange={event => setSearch(event.target.value)} /></label>
        <label>任务状态<select value={status} disabled={working} onChange={event => { setStatus(event.target.value); setOffset(0); }}>
          <option value="">全部状态</option>{STATUSES.map(value => <option key={value} value={value}>{statusLabel(value)}</option>)}
        </select></label>
        <label>每页数量<select value={limit} disabled={working} onChange={event => { setLimit(Number(event.target.value)); setOffset(0); }}>
          {[25, 50, 100, 200].map(value => <option key={value} value={value}>{value} 条</option>)}
        </select></label>
      </div>
      <div className="panel-heading queue-toolbar">
        <div className="actions">
          {trashed ? <>
            <button disabled={working || !targets.length} onClick={() => void move(targets.map(task => task.id), "restore")}>恢复所选任务</button>
            <button className="danger-button" disabled={working || !targets.length} onClick={() => setConfirmation({ kind: "purge", ids: targets.map(task => task.id) })}>永久删除所选任务</button>
            <button className="danger-button" disabled={working} onClick={() => setConfirmation({ kind: "expired" })}>清理过期项</button>
          </> : <>
            <button disabled={working || !targets.length} onClick={() => void move(targets.map(task => task.id), "trash")}>移入回收站</button>
            <button disabled={working || !selectedTerminal.length} onClick={() => setConfirmation({ kind: "cache", all: false })}>清除所选缓存</button>
            <button disabled={working} onClick={() => setConfirmation({ kind: "cache", all: true })}>清除全部已结束缓存</button>
          </>}
        </div>
        <small>已选 {targets.length} 项；选择仅限当前页，切换页面或筛选后清空。</small>
      </div>
      {feed.loading ? <div className="state-card">正在加载任务…</div> : !tasks.length ? <div className="state-card">{term || status ? "没有符合条件的任务。" : trashed ? "回收站为空。" : "队列为空。"}</div> : <div className="history-viewport" ref={viewport} tabIndex={0} aria-label="任务列表" onScroll={event => setScrollTop(event.currentTarget.scrollTop)}>
        <table className="history-table" aria-rowcount={total + 1}>
          <thead><tr>
            <th><input type="checkbox" aria-label="选择本页可操作任务" disabled={working || !selectable.length} checked={selectable.length > 0 && selectable.every(task => selected.has(task.id))} onChange={event => setSelected(event.target.checked ? new Set(selectable.map(task => task.id)) : new Set())} /></th>
            <th>任务</th><th>状态</th><th>阶段 / 进度</th><th>{trashed ? "移入 / 保留到" : "创建时间"}</th><th>操作</th>
          </tr></thead>
          <tbody>
            {windowRows.before > 0 && <tr aria-hidden="true" className="virtual-spacer"><td colSpan={6} style={{ height: windowRows.before }} /></tr>}
            {tasks.slice(windowRows.start, windowRows.end).map((task, index) => <tr key={task.id} className="history-row" aria-rowindex={actualOffset + windowRows.start + index + 2}>
              <td><input type="checkbox" aria-label={`选择任务 ${task.id}`} disabled={working || ["PROCESSING", "DELETING"].includes(task.status)} checked={selected.has(task.id)} onChange={event => setSelected(current => {
                const next = new Set(current);
                if (event.target.checked) next.add(task.id); else next.delete(task.id);
                return next;
              })} /></td>
              <td><strong className="history-title" title={task.title}>{task.title}</strong><small>#{task.id} · {task.source_type}</small></td>
              <td><StatusBadge status={task.status} />{task.error_code?.startsWith("delete_") && <small title={task.error_message ?? ""}>删除未完成；文件可能不完整</small>}</td>
              <td><small>{task.current_stage}</small><div className="progress"><span style={{ width: `${task.progress}%` }} /></div><small>{task.progress}%</small></td>
              <td>{formatDate(trashed ? task.deleted_at ?? null : task.created_at)}{trashed && <small>保留到 {formatDate(task.trash_expires_at ?? null)}</small>}</td>
              <td><div className="actions">
                {trashed ? <>
                  <button disabled={working || task.status === "DELETING"} onClick={() => void move([task.id], "restore")}>恢复</button>
                  <button className="danger-button" disabled={working || task.status === "DELETING"} onClick={() => setConfirmation({ kind: "purge", ids: [task.id] })}>永久删除</button>
                </> : <>
                  <button onClick={() => { window.location.hash = `/tasks/${task.id}`; }}>详情</button>
                  {task.status === "WAITING_REVIEW" && <button onClick={() => { window.location.hash = `/tasks/${task.id}`; }}>校对 / 完成</button>}
                  {task.status === "QUEUED" && !task.review_submission_path && <button onClick={() => { window.location.hash = `/tasks/${task.id}/edit`; }}>编辑</button>}
                  {["QUEUED", "PROCESSING", "WAITING_REVIEW"].includes(task.status) && <button disabled={working} onClick={() => void act(task, "cancel")}>取消</button>}
                  {["FAILED", "INTERRUPTED"].includes(task.status) && <button disabled={working} onClick={() => void act(task, "retry")}>重试</button>}
                  {!["PROCESSING", "DELETING"].includes(task.status) && <button disabled={working} onClick={() => void move([task.id], "trash")}>移入回收站</button>}
                </>}
              </div></td>
            </tr>)}
            {windowRows.after > 0 && <tr aria-hidden="true" className="virtual-spacer"><td colSpan={6} style={{ height: windowRows.after }} /></tr>}
          </tbody>
        </table>
      </div>}
      <div className="history-pagination">
        <span>共 {total} 条 · 当前 {total ? actualOffset + 1 : 0}–{actualOffset + tasks.length} 条</span>
        <div className="actions">
          <button disabled={working || feed.loading || actualOffset === 0} onClick={() => setOffset(Math.max(0, actualOffset - limit))}>上一页</button>
          <span>第 {Math.floor(actualOffset / limit) + 1} / {Math.max(1, Math.ceil(total / limit))} 页</span>
          <button disabled={working || feed.loading || actualOffset + limit >= total} onClick={() => setOffset(actualOffset + limit)}>下一页</button>
        </div>
      </div>
    </section>
  </>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}
