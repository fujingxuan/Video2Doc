import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api/client";
import { Layout } from "./components/Layout";
import { StateView } from "./components/StateView";
import { ModelsPage } from "./pages/ModelsPage";
import { GatewaysPage } from "./pages/GatewaysPage";
import { NewTaskPage } from "./pages/NewTaskPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ProfilesPage } from "./pages/ProfilesPage";
import { QueuePage } from "./pages/QueuePage";
import { SettingsPage } from "./pages/SettingsPage";
import { TaskDetailPage } from "./pages/TaskDetailPage";
import { TaskReviewPage } from "./pages/TaskReviewPage";
import { ManualReviewPage } from "./pages/ManualReviewPage";
import { useTaskFeed } from "./hooks/useTaskFeed";
import type { GatewayConfig, GatewayModel, ModelConfig, ProcessingProfile } from "./types";

function currentPath(): string {
  return window.location.hash.replace(/^#/, "") || "/";
}

export default function App() {
  const [path, setPath] = useState(currentPath);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [gateways, setGateways] = useState<GatewayConfig[]>([]);
  const [gatewayModels, setGatewayModels] = useState<GatewayModel[]>([]);
  const [profiles, setProfiles] = useState<ProcessingProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const feed = useTaskFeed({ limit: 6 }, path === "/");
  const tasks = feed.data?.page.items ?? [];
  const queue = feed.data?.queue ?? null;
  const connected = feed.connected;
  const refreshTasks = feed.refresh;
  const refreshCatalog = useCallback(async () => {
    const [nextModels, nextProfiles, nextGateways, nextGatewayModels] = await Promise.all([
      api.models(), api.profiles(), api.gateways(), api.gatewayModels(),
    ]);
    setModels(nextModels); setProfiles(nextProfiles); setGateways(nextGateways); setGatewayModels(nextGatewayModels);
  }, []);

  useEffect(() => {
    const onHashChange = () => setPath(currentPath());
    window.addEventListener("hashchange", onHashChange);
    let retired = false;
    let retry: number | undefined;
    const initialize = async () => {
      try {
        await Promise.all([refreshCatalog(), api.health()]);
        if (!retired) { setError(null); setLoading(false); }
      } catch (reason) {
        if (!retired) {
          setError(reason instanceof Error ? reason.message : "无法连接后端");
          setLoading(false);
          retry = window.setTimeout(() => void initialize(), 2000);
        }
      }
    };
    void initialize();
    return () => {
      retired = true;
      window.clearTimeout(retry);
      window.removeEventListener("hashchange", onHashChange);
    };
  }, [refreshCatalog]);

  const content = useMemo(() => {
    const edit = path.match(/^\/tasks\/(\d+)\/edit$/);
    if (edit) return <NewTaskPage key={edit[1]} editTaskId={Number(edit[1])} profiles={profiles} gatewayModels={gatewayModels} models={models} onCreated={refreshTasks} />;
    const review = path.match(/^\/tasks\/(\d+)\/review$/);
    const manual = path.match(/^\/tasks\/(\d+)\/manual-review$/);
    if (manual) return <ManualReviewPage key={manual[1]} taskId={Number(manual[1])} />;
    if (review) return <TaskReviewPage key={review[1]} taskId={Number(review[1])} />;
    const detail = path.match(/^\/tasks\/(\d+)$/);
    if (detail) return <TaskDetailPage key={detail[1]} taskId={Number(detail[1])} />;
    switch (path) {
      case "/": return <OverviewPage queue={queue} tasks={tasks} connected={connected} />;
      case "/new": return <NewTaskPage profiles={profiles} gatewayModels={gatewayModels} models={models} onCreated={refreshTasks} />;
      case "/queue": return <QueuePage key="active" />;
      case "/trash": return <QueuePage key="trash" trashed />;
      case "/llm":
      case "/models": return <ModelsPage models={models} refresh={refreshCatalog} />;
      case "/ocr": return <GatewaysPage key="OCR" gatewayType="OCR" gateways={gateways} gatewayModels={gatewayModels} refresh={refreshCatalog} />;
      case "/asr":
      case "/gateways": return <GatewaysPage key="ASR" gatewayType="ASR" gateways={gateways} gatewayModels={gatewayModels} refresh={refreshCatalog} />;
      case "/profiles": return <ProfilesPage profiles={profiles} models={models} gatewayModels={gatewayModels} refresh={refreshCatalog} />;
      case "/settings": return <SettingsPage />;
      default: return <div className="state-card">页面不存在。<button onClick={() => { window.location.hash = "/"; }}>返回总览</button></div>;
    }
  }, [connected, gatewayModels, gateways, models, path, profiles, queue, refreshCatalog, refreshTasks, tasks]);

  const navPath = path.match(/^\/tasks\//)
    ? "/queue"
    : path === "/models"
      ? "/llm"
      : path === "/gateways"
        ? "/asr"
        : path;
  return <Layout path={navPath}><StateView loading={loading || feed.loading} error={error || (path === "/" ? feed.error : null)}>{content}</StateView></Layout>;
}
