import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE, api } from "../api/client";
import type { TaskFeed, TaskPageDelta, TaskPageQuery } from "../types";
import { applyTaskDelta, taskPageQuery } from "../utils/taskFeed";

export function useTaskFeed(query: TaskPageQuery, enabled = true) {
  const [snapshot, setSnapshot] = useState<{ key: string; data: TaskFeed } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const key = taskPageQuery(query);
  // Never expose the preceding filter's page during the render before effect
  // cleanup. In particular, it must not reset a newly selected offset.
  const data = enabled && snapshot?.key === key ? snapshot.data : null;
  const refresh = useCallback(() => refreshRef.current(), []);

  useEffect(() => {
    setSnapshot(null);
    setError(null);
    setConnected(false);
    if (!enabled) return;
    let retired = false;
    let live = false;
    let current: TaskFeed | null = null;
    let stream: EventSource | null = null;
    let controller: AbortController | null = null;
    let requestId = 0;
    let reconnectTimer: number | undefined;

    const accept = (value: TaskFeed) => {
      if (retired) return;
      current = value;
      setSnapshot({ key, data: value });
      setError(null);
    };
    const reload = async () => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      const id = ++requestId;
      try {
        const [page, queue] = await Promise.all([api.taskPage(key, request.signal), api.queue()]);
        if (!retired && id === requestId) accept({ page, queue });
      } catch (reason) {
        if (!retired && !request.signal.aborted && id === requestId) {
          setError(reason instanceof Error ? reason.message : "加载任务失败");
        }
      } finally {
        if (controller === request) controller = null;
      }
    };
    const connect = () => {
      if (retired) return;
      stream?.close();
      const source = new EventSource(`${API_BASE}/events/pages?${key}`);
      stream = source;
      live = false;
      setConnected(false);
      const receive = (event: Event, full: boolean) => {
        if (retired || stream !== source) return;
        try {
          const payload = JSON.parse((event as MessageEvent<string>).data);
          if (full) {
            if (!Array.isArray(payload.page?.items)) throw new Error("Invalid page snapshot");
            accept(payload as TaskFeed);
          } else {
            if (!current) throw new Error("Missing page snapshot");
            accept(applyTaskDelta(current, payload as TaskPageDelta));
          }
          ++requestId;
          controller?.abort();
          live = true;
          setConnected(true);
        } catch {
          source.close();
          live = false;
          setConnected(false);
          reconnectTimer = window.setTimeout(connect, 1000);
        }
      };
      source.addEventListener("page", event => receive(event, true));
      source.addEventListener("delta", event => receive(event, false));
      source.onerror = () => {
        if (retired || stream !== source) return;
        live = false;
        setConnected(false);
      };
    };
    refreshRef.current = async () => {
      // Reopen the stream after mutations so subsequent deltas are based on
      // the same full page as the manual refresh, including changed membership.
      connect();
      await reload();
    };
    connect();
    void reload();
    const timer = window.setInterval(() => {
      if (!live && !controller) void reload();
    }, 2000);
    return () => {
      retired = true;
      stream?.close();
      controller?.abort();
      window.clearInterval(timer);
      window.clearTimeout(reconnectTimer);
      refreshRef.current = async () => {};
    };
  }, [key, enabled]);

  return { data, error, connected, loading: enabled && !data && !error, refresh };
}
