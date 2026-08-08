import { describe, expect, it } from "vitest";

import { markdownBody, workspaceMarkdownImagePath } from "./markdown";

describe("markdown preview helpers", () => {
  it("hides YAML frontmatter from the rendered article", () => {
    expect(markdownBody("---\ntitle: demo\n---\n# 正文\n")).toBe("# 正文\n");
    expect(markdownBody("# 正文\n")).toBe("# 正文\n");
  });

  it("maps only existing workspace-relative images", () => {
    const artifacts = ["05_output/课程.assets/frame 01.jpg"];
    expect(workspaceMarkdownImagePath("课程.assets/frame%2001.jpg", artifacts)).toBe(
      artifacts[0],
    );
    expect(workspaceMarkdownImagePath("../secret.jpg", artifacts)).toBeNull();
    expect(workspaceMarkdownImagePath("https://example.com/image.jpg", artifacts)).toBeNull();
  });
});
