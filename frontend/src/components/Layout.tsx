import type { ReactNode } from "react";

const links = [
  ["/", "总览"],
  ["/new", "新建任务"],
  ["/queue", "任务队列"],
  ["/models", "模型管理"],
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
          <div><strong>Video2Doc</strong><small>真实媒体 + ASR · v0.2</small></div>
        </div>
        <nav>
          {links.map(([target, label]) => (
            <button key={target} className={path === target ? "active" : ""} onClick={() => navigate(target)}>{label}</button>
          ))}
        </nav>
        <p className="scope-note">当前支持真实媒体获取、音频标准化、分块与 OneAPI 兼容 ASR；OCR 和文本整理仍未启用。</p>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
