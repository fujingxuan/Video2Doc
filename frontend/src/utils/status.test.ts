import { describe, expect, it } from "vitest";

import { statusLabel } from "./status";

describe("statusLabel", () => {
  it("maps persistent queue states to user-facing labels", () => {
    expect(statusLabel("QUEUED")).toBe("等待中");
    expect(statusLabel("INTERRUPTED")).toBe("已中断");
  });
});
