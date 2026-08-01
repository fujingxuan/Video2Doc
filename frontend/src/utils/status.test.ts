import { describe, expect, it } from "vitest";

import { formatDuration, parseApiDate, statusLabel } from "./status";

describe("statusLabel", () => {
  it("maps persistent queue states to user-facing labels", () => {
    expect(statusLabel("QUEUED")).toBe("等待中");
    expect(statusLabel("INTERRUPTED")).toBe("已中断");
  });

  it("treats timezone-less backend datetimes as UTC", () => {
    expect(parseApiDate("2026-08-01T12:25:24")).toBe(
      Date.parse("2026-08-01T12:25:24Z"),
    );
    expect(formatDuration(
      "2026-08-01T12:25:24",
      null,
      Date.parse("2026-08-01T12:26:24Z"),
    )).toBe("60.0 秒");
  });
});
