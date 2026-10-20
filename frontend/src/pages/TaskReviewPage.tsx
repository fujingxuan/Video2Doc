import { useEffect, useState } from "react";

import { api } from "../api/client";
import { StateView } from "../components/StateView";
import type { TaskReview } from "../types";

export function TaskReviewPage({ taskId }: { taskId: number }) {
  const [review, setReview] = useState<TaskReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"compare" | "diff">("compare");
  useEffect(() => {
    const controller = new AbortController();
    void api.taskReview(taskId, controller.signal)
      .then(value => { if (!controller.signal.aborted) setReview(value); })
      .catch(reason => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "无法读取审阅资料");
      });
    return () => controller.abort();
  }, [taskId]);

  return <>
    <header className="page-header">
      <div>
        <p className="eyebrow">原文与成品 · 按需审阅</p>
        <h1>整理差异</h1>
        <p>{review?.title ?? `任务 #${taskId}`} · 查看已保存的正文和来源，不会重新调用模型。</p>
      </div>
      <button onClick={() => { window.location.hash = `/tasks/${taskId}`; }}>返回任务详情</button>
    </header>
    <StateView loading={!review && !error} error={error}>{review && <>
      <section className="panel review-summary">
        <div className="tags">
          <span>{review.manual_review ? "用户确认稿件" : review.fallback_used ? "已回退为原文" : review.accepted ? "规则校验通过" : "查看保存结果"}</span>
          <span>{review.changed ? "正文有变化" : "正文无变化"}</span>
          <span>{review.source_count} 个来源</span>
          <span>{review.model ? `模型：${review.model}` : "确定性整理"}</span>
        </div>
        <p>规则校验不能替代语义判断。右侧显示实际采用的整理正文；模型输出被拒绝时，这里显示回退后的正文。</p>
        {review.reason && <details><summary>整理记录</summary><pre>{review.reason}</pre></details>}
        <div className="actions">
          <button aria-pressed={view === "compare"} onClick={() => setView("compare")}>并排对照</button>
          <button aria-pressed={view === "diff"} onClick={() => setView("diff")}>逐行差异</button>
          <a className="button-link" href={api.reviewDiffUrl(taskId)} download>下载完整差异</a>
        </div>
      </section>
      {view === "compare" ? <section className="review-columns">
        <article className="panel"><h2>原始正文 <small>{review.original_characters.toLocaleString()} 字符</small></h2>
          <pre className="review-text" tabIndex={0}>{review.original_text || "（无文本）"}</pre>
        </article>
        <article className="panel"><h2>实际采用的正文 <small>{review.edited_characters.toLocaleString()} 字符</small></h2>
          <pre className="review-text" tabIndex={0}>{review.edited_text || "（无文本）"}</pre>
        </article>
      </section> : <section className="panel">
        <h2>逐行差异</h2>
        <p>以 − 开头的行为原文，以 + 开头的行为修改后内容；@@ 标记对应行号。</p>
        <pre className="review-text diff-text" tabIndex={0}>{review.diff || (review.changed ? "仅空行或行末换行不同，请用并排对照查看。" : "正文无变化。")}</pre>
      </section>}
    </>}</StateView>
  </>;
}
