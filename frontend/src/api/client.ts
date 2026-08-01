import type {
  AppSettings,
  CacheClearResult,
  Capability,
  GatewayConfig,
  GatewayModel,
  GatewayType,
  ModelConfig,
  ProcessingProfile,
  QueueState,
  Task,
  TaskDetail,
  SystemStatus,
} from "../types";

const defaultApiBase = ["5173", "4173"].includes(window.location.port)
  ? "http://127.0.0.1:8000/api/v1"
  : `${window.location.origin}/api/v1`;
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? defaultApiBase).replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "无法连接 Video2Doc 后端。请关闭失效窗口后重新启动应用。",
      0,
    );
  }
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const body = (await response.json()) as { error?: { message?: string }; detail?: string };
      message = body.error?.message ?? body.detail ?? message;
    } catch {
      // The status code remains actionable when a non-JSON response is returned.
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () =>
    request<{ status: string; version: string; database: string; queue_worker: string }>(
      "/health",
    ),
  models: (capability?: Capability) =>
    request<ModelConfig[]>(`/models${capability ? `?capability=${capability}` : ""}`),
  createModel: (payload: Record<string, unknown>) =>
    request<ModelConfig>("/models", { method: "POST", body: JSON.stringify(payload) }),
  updateModel: (id: number, payload: Record<string, unknown>) =>
    request<ModelConfig>(`/models/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteModel: (id: number) => request<void>(`/models/${id}`, { method: "DELETE" }),
  gateways: (gatewayType?: GatewayType) =>
    request<GatewayConfig[]>(`/gateways${gatewayType ? `?gateway_type=${gatewayType}` : ""}`),
  createGateway: (payload: Record<string, unknown>) =>
    request<GatewayConfig>("/gateways", { method: "POST", body: JSON.stringify(payload) }),
  updateGateway: (id: number, payload: Record<string, unknown>) =>
    request<GatewayConfig>(`/gateways/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteGateway: (id: number) => request<void>(`/gateways/${id}`, { method: "DELETE" }),
  checkGateway: (id: number) =>
    request<{ gateway_id: number; status: string; detail: string | null }>(
      `/gateways/${id}/health`,
      { method: "POST" },
    ),
  discoverGateway: (id: number) =>
    request<GatewayModel[]>(`/gateways/${id}/discover`, { method: "POST" }),
  gatewayModels: (gatewayType?: GatewayType) =>
    request<GatewayModel[]>(
      `/gateway-models${gatewayType ? `?gateway_type=${gatewayType}` : ""}`,
    ),
  createGatewayModel: (payload: Record<string, unknown>) =>
    request<GatewayModel>("/gateway-models", { method: "POST", body: JSON.stringify(payload) }),
  updateGatewayModel: (id: number, payload: Record<string, unknown>) =>
    request<GatewayModel>(`/gateway-models/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteGatewayModel: (id: number) =>
    request<void>(`/gateway-models/${id}`, { method: "DELETE" }),
  profiles: () => request<ProcessingProfile[]>("/profiles"),
  createProfile: (payload: Record<string, unknown>) =>
    request<ProcessingProfile>("/profiles", { method: "POST", body: JSON.stringify(payload) }),
  updateProfile: (id: number, payload: Record<string, unknown>) =>
    request<ProcessingProfile>(`/profiles/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  deleteProfile: (id: number) => request<void>(`/profiles/${id}`, { method: "DELETE" }),
  settings: () => request<AppSettings>("/settings"),
  updateSettings: (payload: Partial<AppSettings>) =>
    request<AppSettings>("/settings", { method: "PUT", body: JSON.stringify(payload) }),
  tasks: () => request<Task[]>("/tasks"),
  task: (id: number) => request<TaskDetail>(`/tasks/${id}`),
  taskFrames: (id: number) => request<Record<string, unknown>>(`/tasks/${id}/frames`),
  taskOcr: (id: number) => request<Record<string, unknown>>(`/tasks/${id}/ocr`),
  taskTimeline: (id: number) => request<Record<string, unknown>>(`/tasks/${id}/timeline`),
  taskDocument: (id: number) => request<Record<string, unknown>>(`/tasks/${id}/document`),
  openTaskTarget: (id: number, target: "workspace" | "export_directory" | "markdown") =>
    request<{ target: string; path: string }>(`/tasks/${id}/open/${target}`, {
      method: "POST",
    }),
  artifactUrl: (id: number, path: string) =>
    `${API_BASE}/tasks/${id}/artifacts/${path
      .split("/")
      .map(segment => encodeURIComponent(segment))
      .join("/")}`,
  createTasks: (payload: Record<string, unknown>) =>
    request<Task[]>("/tasks", { method: "POST", body: JSON.stringify(payload) }),
  cancelTask: (id: number) => request<Task>(`/tasks/${id}/cancel`, { method: "POST" }),
  retryTask: (id: number) => request<Task>(`/tasks/${id}/retry`, { method: "POST" }),
  queue: () => request<QueueState>("/queue"),
  systemStatus: () => request<SystemStatus>("/system/status"),
  selectMedia: () => request<{ paths: string[] }>("/system/select-media", { method: "POST" }),
  selectSubtitle: () =>
    request<{ path: string | null }>("/system/select-subtitle", { method: "POST" }),
  selectOutputDirectory: () =>
    request<{ path: string | null }>("/system/select-output-directory", { method: "POST" }),
  clearTaskCache: (payload: { task_ids?: number[]; all_tasks?: boolean }) =>
    request<CacheClearResult>("/system/cache/clear", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  shutdownApp: () => request<{ status: string }>("/system/shutdown", { method: "POST" }),
};
