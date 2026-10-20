import type { TaskFeed, TaskPageDelta, TaskPageQuery } from "../types";

export function taskPageQuery(query: TaskPageQuery): string {
  const parameters = new URLSearchParams({
    offset: String(query.offset ?? 0),
    limit: String(query.limit ?? 100),
    trashed: String(query.trashed ?? false),
  });
  if (query.status) parameters.set("status", query.status);
  if (query.search?.trim()) parameters.set("search", query.search.trim());
  return parameters.toString();
}

export function applyTaskDelta(previous: TaskFeed, delta: TaskPageDelta): TaskFeed {
  const items = new Map(previous.page.items.map(item => [item.id, item]));
  for (const id of delta.removed_ids) items.delete(id);
  for (const item of delta.upserts) items.set(item.id, item);
  if (new Set(delta.order).size !== delta.order.length || delta.order.length > delta.limit) {
    throw new Error("Invalid page order");
  }
  const ordered = delta.order.map(id => {
    const item = items.get(id);
    if (!item) throw new Error("Page needs to be synchronized again");
    return item;
  });
  return {
    page: { items: ordered, total: delta.total, offset: delta.offset, limit: delta.limit },
    queue: delta.queue,
  };
}

export function virtualRows(count: number, scrollTop: number, height: number, rowHeight = 112) {
  const start = Math.max(0, Math.min(count, Math.floor(scrollTop / rowHeight) - 4));
  const end = Math.min(count, Math.ceil((scrollTop + height) / rowHeight) + 4);
  return { start, end, before: start * rowHeight, after: (count - end) * rowHeight };
}
