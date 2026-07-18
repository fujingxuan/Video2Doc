import type { ReactNode } from "react";

export function StateView({ loading, error, empty, children }: { loading: boolean; error: string | null; empty?: boolean; children: ReactNode }) {
  if (loading) return <div className="state-card">正在加载…</div>;
  if (error) return <div className="state-card error">{error}</div>;
  if (empty) return <div className="state-card">暂时没有数据。</div>;
  return <>{children}</>;
}
