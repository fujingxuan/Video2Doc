import type {
  AppSettings,
  Capability,
  ModelConfig,
  ProcessingProfile,
  QueueState,
  Task,
  TaskDetail,
} from "../types";

export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api/v1").replace(
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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
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
  health: () => request<{ status: string; database: string; queue_worker: string }>("/health"),
  models: (capability?: Capability) =>
    request<ModelConfig[]>(`/models${capability ? `?capability=${capability}` : ""}`),
  createModel: (payload: {
    name: string;
    capabilities: Capability[];
    notes?: string;
    enabled?: boolean;
  }) => request<ModelConfig>("/models", { method: "POST", body: JSON.stringify(payload) }),
  updateModel: (id: number, payload: Partial<ModelConfig>) =>
    request<ModelConfig>(`/models/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteModel: (id: number) => request<void>(`/models/${id}`, { method: "DELETE" }),
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
  createTasks: (payload: Record<string, unknown>) =>
    request<Task[]>("/tasks", { method: "POST", body: JSON.stringify(payload) }),
  cancelTask: (id: number) => request<Task>(`/tasks/${id}/cancel`, { method: "POST" }),
  retryTask: (id: number) => request<Task>(`/tasks/${id}/retry`, { method: "POST" }),
  queue: () => request<QueueState>("/queue"),
};
