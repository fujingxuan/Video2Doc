export function markdownBody(markdown: string): string {
  const normalized = markdown.replace(/^\uFEFF/, "");
  if (!normalized.startsWith("---\n") && !normalized.startsWith("---\r\n")) {
    return normalized;
  }
  const match = /^---\r?\n[\s\S]*?\r?\n---\r?\n/.exec(normalized);
  return match ? normalized.slice(match[0].length) : normalized;
}

export function workspaceMarkdownImagePath(
  source: string | undefined,
  artifactPaths: readonly string[],
): string | null {
  if (!source || /^[a-z][a-z\d+.-]*:/i.test(source) || source.startsWith("//")) {
    return null;
  }
  const pathOnly = source.split(/[?#]/, 1)[0].replaceAll("\\", "/");
  let decoded = pathOnly;
  try {
    decoded = decodeURIComponent(pathOnly);
  } catch {
    // Keep the original source when a literal percent sign is present.
  }
  const parts = decoded.split("/").filter(part => part && part !== ".");
  if (parts.some(part => part === "..")) return null;
  const normalized = parts.join("/");
  const candidate = normalized.startsWith("05_output/")
    ? normalized
    : `05_output/${normalized}`;
  const actual = artifactPaths.find(path => path.toLocaleLowerCase() === candidate.toLocaleLowerCase());
  return actual ?? null;
}
