import type { TaskStatus } from "../types";
import { statusLabel } from "../utils/status";

export function StatusBadge({ status }: { status: TaskStatus }) {
  return <span className={`status status-${status.toLowerCase()}`}>{statusLabel(status)}</span>;
}
