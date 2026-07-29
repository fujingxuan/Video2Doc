import type { ReactNode } from "react";
import packageInfo from "../../package.json";

const links = [
  ["/", "总览"],
  ["/new", "新建任务"],
  ["/queue", "任务队列"],
  ["/models", "模型管理"],
  ["/gateways", "Gateway"],
  ["/profiles", "处理方案"],
  ["/settings", "应用设置"],
];

export function Layout({ path, children }: { path: string; children: ReactNode }) {
  const navigate = (target: string) => {
    window.location.hash = target;
  };
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">V2D</span>
          <div><strong>Video2Doc</strong><small>忠实转换与 Obsidian 导出 · v{packageInfo.version}</small></div>
        </div>
        <nav>
          {links.map(([target, label]) => (
            <button key={target} className={path === target ? "active" : ""} onClick={() => navigate(target)}>{label}</button>
          ))}
        </nav>
        <p className="scope-note">ASR 与 OCR 使用独立 Gateway；OneAPI 只用于可选的忠实轻度整理。应用不生成摘要、问答或知识库索引。</p>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
