import type { TaskStatus } from "../types";

const LABELS: Record<TaskStatus, string> = {
  QUEUED: "等待中",
  PROCESSING: "处理中",
  WAITING_REVIEW: "待人工校对",
  DELETING: "正在永久删除",
  COMPLETED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  INTERRUPTED: "已中断",
};

export function statusLabel(status: TaskStatus): string {
  return LABELS[status];
}

export function parseApiDate(value: string): number {
  const hasTimezone = /(?:z|[+-]\d{2}:?\d{2})$/i.test(value);
  return Date.parse(hasTimezone ? value : `${value}Z`);
}

export function formatDate(value: string | null): string {
  return value
    ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "medium" })
      .format(new Date(parseApiDate(value)))
    : "—";
}

export function formatDuration(
  start: string | null,
  end: string | null,
  now = Date.now(),
): string {
  if (!start) return "—";
  const seconds = ((end ? parseApiDate(end) : now) - parseApiDate(start)) / 1000;
  return Number.isFinite(seconds) ? `${Math.max(0, seconds).toFixed(1)} 秒` : "—";
}
