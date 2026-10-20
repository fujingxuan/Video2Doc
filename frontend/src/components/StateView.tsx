import type { ReactNode } from "react";

export function StateView({ loading, error, empty, children }: { loading: boolean; error: string | null; empty?: boolean; children: ReactNode }) {
  if (loading) return <div className="state-card loading-state" role="status"><span className="loading-spinner" />正在加载工作空间…</div>;
  if (error) return <div className="state-card error" role="alert">{error}</div>;
  if (empty) return <div className="state-card">暂时没有数据。</div>;
  return <>{children}</>;
}
