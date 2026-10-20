import type { ReactNode } from "react";
import packageInfo from "../../package.json";
import { Icon, type IconName } from "./Icon";

const groups: { label: string; links: { target: string; label: string; icon: IconName }[] }[] = [
  { label: "工作空间", links: [
    { target: "/", label: "任务总览", icon: "overview" },
    { target: "/new", label: "新建任务", icon: "plus" },
    { target: "/queue", label: "任务队列", icon: "queue" },
    { target: "/trash", label: "回收站", icon: "trash" },
  ] },
  { label: "模型与处理", links: [
    { target: "/asr", label: "ASR 语音识别", icon: "audio" },
    { target: "/ocr", label: "OCR 画面识别", icon: "scan" },
    { target: "/llm", label: "LLM 文本整理", icon: "text" },
    { target: "/profiles", label: "处理方案", icon: "layers" },
  ] },
];

export function Layout({ path, children }: { path: string; children: ReactNode }) {
  const navigate = (target: string) => {
    window.location.hash = target;
  };
  const current = groups.flatMap(group => group.links).find(link => link.target === path)?.label ?? "应用设置";
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#/" aria-label="Video2Doc 任务总览">
          <span className="brand-mark"><Icon name="play" width="27" height="27" /></span>
          <span><strong>Video2Doc<span className="brand-dot">.</span></strong><small>让内容，沉淀为文字</small></span>
        </a>
        <nav aria-label="主导航">
          {groups.map(group => <div className="nav-group" key={group.label}>
            <p className="nav-label">{group.label}</p>
            {group.links.map(({ target, label, icon }) => <button key={target} aria-current={path === target ? "page" : undefined} className={path === target ? "active" : ""} onClick={() => navigate(target)}><Icon name={icon} /><span>{label}</span>{path === target && <span className="nav-dot" />}</button>)}
          </div>)}
        </nav>
        <div className="sidebar-footer">
          <div className="workspace-note"><Icon name="shield" /><div><strong>忠实保留原始内容</strong><small>完整转写 · 人工校对 · Markdown</small></div></div>
          <button className={`settings-nav ${path === "/settings" ? "active" : ""}`} aria-current={path === "/settings" ? "page" : undefined} onClick={() => navigate("/settings")}><Icon name="settings" /><span>应用设置</span><small>v{packageInfo.version}</small></button>
        </div>
      </aside>
      <div className="workspace">
        <div className="workspace-bar"><div className="breadcrumb">工作空间<span>/</span><strong>{current}</strong></div><div className="workspace-actions"><span className="local-label"><span />本地工作台</span><button className="primary compact" onClick={() => navigate("/new")}><Icon name="plus" width="16" height="16" />新建任务</button></div></div>
        <main id="main-content" className="main">{children}</main>
      </div>
    </div>
  );
}
