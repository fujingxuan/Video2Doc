import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { api } from "../api/client";
import { markdownBody, workspaceMarkdownImagePath } from "../utils/markdown";

const previewSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "details", "summary"],
};

export function MarkdownPreview({
  markdown,
  taskId,
  artifactPaths,
}: {
  markdown: string;
  taskId: number;
  artifactPaths: readonly string[];
}) {
  return (
    <div className="markdown-preview">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, previewSchema]]}
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          img: ({ src, alt, ...props }) => {
            const artifactPath = workspaceMarkdownImagePath(src, artifactPaths);
            return (
              <img
                {...props}
                src={artifactPath ? api.artifactUrl(taskId, artifactPath) : src}
                alt={alt ?? ""}
                loading="lazy"
              />
            );
          },
        }}
      >
        {markdownBody(markdown)}
      </ReactMarkdown>
    </div>
  );
}
