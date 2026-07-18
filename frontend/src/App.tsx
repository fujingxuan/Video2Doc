import { useCallback, useEffect, useMemo, useState } from "react";

import { API_BASE, api } from "./api/client";
import { Layout } from "./components/Layout";
import { StateView } from "./components/StateView";
import { ModelsPage } from "./pages/ModelsPage";
import { NewTaskPage } from "./pages/NewTaskPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { QueuePage } from "./pages/QueuePage";
import { SettingsPage } from "./pages/SettingsPage";
import { TaskDetailPage } from "./pages/TaskDetailPage";
import type { ModelConfig, ProcessingProfile, QueueState, Task } from "./types";

function currentPath(): string {
  return window.location.hash.replace(/^#/, "") || "/";
}

export default function App() {
  const [path, setPath] = useState(currentPath);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [profiles, setProfiles] = useState<ProcessingProfile[]>([]);
  const [queue, setQueue] = useState<QueueState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  const refreshTasks = useCallback(async () => {
    const [nextTasks, nextQueue] = await Promise.all([api.tasks(), api.queue()]);
    setTasks(nextTasks); setQueue(nextQueue);
  }, []);
  const refreshCatalog = useCallback(async () => {
    const [nextModels, nextProfiles] = await Promise.all([api.models(), api.profiles()]);
    setModels(nextModels); setProfiles(nextProfiles);
  }, []);

  useEffect(() => {
    const onHashChange = () => setPath(currentPath());
    window.addEventListener("hashchange", onHashChange);
    Promise.all([refreshTasks(), refreshCatalog(), api.health()])
      .then(() => { setError(null); setLoading(false); })
      .catch(reason => { setError(reason instanceof Error ? reason.message : "无法连接后端"); setLoading(false); });
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [refreshCatalog, refreshTasks]);

  useEffect(() => {
    const stream = new EventSource(`${API_BASE}/events`);
    stream.addEventListener("state", event => {
      try {
        const payload = JSON.parse((event as MessageEvent<string>).data) as { tasks: Task[]; queue: QueueState };
        setTasks(payload.tasks); setQueue(payload.queue); setConnected(true); setError(null); setLoading(false);
      } catch {
        setConnected(false);
      }
    });
    stream.onerror = () => setConnected(false);
    stream.onopen = () => setConnected(true);
    return () => stream.close();
  }, []);

  useEffect(() => {
    if (connected) return;
    const recover = () => {
      void refreshTasks()
        .then(() => { setError(null); setLoading(false); })
        .catch(() => { /* EventSource and the next fallback poll will retry. */ });
    };
    recover();
    const timer = window.setInterval(recover, 2000);
    return () => window.clearInterval(timer);
  }, [connected, refreshTasks]);

  const content = useMemo(() => {
    const detail = path.match(/^\/tasks\/(\d+)$/);
    if (detail) return <TaskDetailPage taskId={Number(detail[1])} />;
    switch (path) {
      case "/": return <OverviewPage queue={queue} tasks={tasks} connected={connected} />;
      case "/new": return <NewTaskPage profiles={profiles} models={models} onCreated={refreshTasks} />;
      case "/queue": return <QueuePage tasks={tasks} refresh={refreshTasks} />;
      case "/models": return <ModelsPage models={models} refresh={refreshCatalog} />;
      case "/profiles": return <ProfilesPage profiles={profiles} models={models} refresh={refreshCatalog} />;
      case "/settings": return <SettingsPage />;
      default: return <div className="state-card">页面不存在。<button onClick={() => { window.location.hash = "/"; }}>返回总览</button></div>;
    }
  }, [connected, models, path, profiles, queue, refreshCatalog, refreshTasks, tasks]);

  const navPath = path.match(/^\/tasks\//) ? "/queue" : path;
  return <Layout path={navPath}><StateView loading={loading} error={error}>{content}</StateView></Layout>;
}
