import { describe, expect, it } from "vitest";

import type { QueueState, Task, TaskFeed, TaskPageDelta } from "../types";
import { applyTaskDelta, taskPageQuery, virtualRows } from "./taskFeed";

const queue: QueueState = { max_concurrency: 1, running: 0, queued: 0, completed: 3, failed: 0, cancelled: 0, interrupted: 0, running_task_id: null };
const rows = [3, 2, 1].map(id => ({ id, title: `task ${id}`, progress: 0 }) as Task);
const feed: TaskFeed = { page: { items: rows, total: 3, offset: 0, limit: 100 }, queue };
const delta: TaskPageDelta = { upserts: [{ ...rows[1], progress: 42 }, { ...rows[0], id: 4 }], removed_ids: [1], order: [4, 3, 2], total: 3, offset: 0, limit: 100, queue };

describe("paged task updates", () => {
  it("updates changed rows and membership without mutating previous data", () => {
    const next = applyTaskDelta(feed, delta);
    expect(next.page.items.map(task => task.id)).toEqual([4, 3, 2]);
    expect(next.page.items[1]).toBe(rows[0]);
    expect(next.page.items[2].progress).toBe(42);
    expect(feed.page.items.map(task => task.id)).toEqual([3, 2, 1]);
    expect(rows[1].progress).toBe(0);
  });
  it("resynchronizes when delta references missing or duplicate rows", () => {
    expect(() => applyTaskDelta(feed, { ...delta, order: [99] })).toThrow();
    expect(() => applyTaskDelta(feed, { ...delta, order: [3, 3] })).toThrow();
    expect(() => applyTaskDelta(feed, { ...delta, limit: 1 })).toThrow();
  });
  it("handles empty pages and total-only changes", () => {
    const next = applyTaskDelta(feed, { ...delta, upserts: [], removed_ids: [3, 2, 1], order: [], total: 0 });
    expect(next.page.items).toEqual([]);
    expect(next.page.total).toBe(0);
  });
  it("encodes literal search and keeps page/filter parameters", () => {
    const parameters = new URLSearchParams(taskPageQuery({ search: " 原文&100%_ ", offset: 200, limit: 100, status: "FAILED", trashed: true }));
    expect(parameters.get("search")).toBe("原文&100%_");
    expect(parameters.get("offset")).toBe("200");
    expect(parameters.get("trashed")).toBe("true");
    expect(parameters.get("status")).toBe("FAILED");
  });
});

describe("virtual rows", () => {
  it("renders only the viewport plus a bounded overscan", () => {
    const result = virtualRows(200, 112 * 100, 560);
    expect(result).toEqual({ start: 96, end: 109, before: 10752, after: 10192 });
    expect(result.before + (result.end - result.start) * 112 + result.after).toBe(22400);
  });
  it("keeps first, last and empty windows within bounds", () => {
    expect(virtualRows(0, 0, 560)).toEqual({ start: 0, end: 0, before: 0, after: 0 });
    expect(virtualRows(200, 0, 560).start).toBe(0);
    const last = virtualRows(200, 21900, 560);
    expect(last.end).toBe(200);
    expect(last.after).toBe(0);
    expect(virtualRows(5, 21900, 560)).toEqual({ start: 5, end: 5, before: 560, after: 0 });
  });
});
