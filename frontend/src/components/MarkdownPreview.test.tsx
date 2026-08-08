import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("MarkdownPreview", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders GFM safely and resolves workspace images through the artifact API", async () => {
    vi.stubGlobal("window", {
      location: { port: "5173", origin: "http://localhost:5173" },
    });
    const { MarkdownPreview } = await import("./MarkdownPreview");
    const markdown = [
      "---",
      "title: hidden metadata",
      "---",
      "# 正文",
      "",
      "| 列一 | 列二 |",
      "| --- | --- |",
      "| A | B |",
      "",
      "<script>alert('unsafe')</script>",
      "",
      "<details><summary>追溯信息</summary>内容</details>",
      "",
      "![画面](课程.assets/frame%2001.jpg)",
    ].join("\n");

    const html = renderToStaticMarkup(
      createElement(MarkdownPreview, {
        markdown,
        taskId: 8,
        artifactPaths: ["05_output/课程.assets/frame 01.jpg"],
      }),
    );

    expect(html).toContain("<h1>正文</h1>");
    expect(html).toContain("<table>");
    expect(html).toContain("<details><summary>追溯信息</summary>内容</details>");
    expect(html).toContain("/tasks/8/artifacts/05_output/");
    expect(html).toContain("frame%2001.jpg");
    expect(html).not.toContain("hidden metadata");
    expect(html).not.toContain("<script>");
  });
});
