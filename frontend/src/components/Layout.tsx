import type { ReactNode } from "react";

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
          <div><strong>Video2Doc</strong><small>独立 ASR/OCR Gateway · v0.3</small></div>
        </div>
        <nav>
          {links.map(([target, label]) => (
            <button key={target} className={path === target ? "active" : ""} onClick={() => navigate(target)}>{label}</button>
          ))}
        </nav>
        <p className="scope-note">ASR 与 OCR 使用独立 Gateway；OneAPI 仅保留给后续文本处理。OCR Provider 本轮只完成架构和 mock 验证。</p>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
