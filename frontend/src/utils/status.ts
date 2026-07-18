import type { TaskStatus } from "../types";

const LABELS: Record<TaskStatus, string> = {
  QUEUED: "等待中",
  PROCESSING: "处理中",
  COMPLETED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  INTERRUPTED: "已中断",
};

export function statusLabel(status: TaskStatus): string {
  return LABELS[status];
}

export function formatDate(value: string | null): string {
  return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value)) : "—";
}
