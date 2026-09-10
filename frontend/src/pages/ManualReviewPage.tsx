import { useEffect, useState } from "react";

import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StateView } from "../components/StateView";
import type { ManualReview } from "../types";

export function ManualReviewPage({ taskId }: { taskId: number }) {
  const [review, setReview] = useState<ManualReview | null>(null);
  const [edited, setEdited] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{ action: "original" | "edited"; text: string } | null>(null);

  useEffect(() => {
    let active = true;
    void api.manualReview(taskId).then(value => {
      if (active) { setReview(value); setEdited(value.edited_text); setSaved(value.edited_text); }
    }).catch(reason => { if (active) setError(String(reason.message ?? reason)); });
    return () => { active = false; };
  }, [taskId]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    if (edited !== saved) window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [edited, saved]);

  const payload = () => ({ attempt: review!.attempt, source_sha256: review!.source_sha256, edited_text: edited });
  const copy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setMessage("已复制。"); }
    catch { setMessage("浏览器未允许复制，请在下方文本框中全选并复制。"); }
  };
  const save = async () => {
    setBusy(true); setMessage(null);
    try { await api.saveManualReview(taskId, payload()); setSaved(edited); setMessage("修改稿已保存，重新打开应用后可继续校对。"); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  };
  const submit = async (action: "original" | "edited", acknowledge = false) => {
    setConfirmation(null); setBusy(true); setMessage(null);
    try {
      const result = await api.submitManualReview(taskId, { ...payload(), action, acknowledge_warnings: acknowledge });
      if (result.requires_confirmation) {
        setConfirmation({ action, text: `检测到以下变化：${result.warnings.map(item => item.message).join("；")}。请确认这些修改符合原音频内容。确认后将采用修改稿，初稿仍会保留。` });
      } else {
        setSaved(edited);
        window.location.hash = `/tasks/${taskId}`;
      }
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "提交失败"); }
    finally { setBusy(false); }
  };
  const importText = async (file?: File) => {
    if (!file) return;
    if (!/\.(md|txt)$/i.test(file.name) || file.size > 8_000_000) {
      setMessage("请选择不超过 8 MB 的 UTF-8 Markdown 或 TXT 文件。"); return;
    }
    try {
      const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()).replace(/\r\n?/g, "\n");
      if (text.length > 2_000_000) throw new Error("正文不能超过 200 万字。");
      setEdited(text); setMessage("已导入修改稿，请检查后保存或完成任务。");
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : "读取失败，请使用 UTF-8 文本"); }
  };

  return <StateView loading={!review && !error} error={error}>{review && <>
    <header className="page-header"><div><p className="eyebrow">待人工校对 · 任务 #{taskId}</p>
      <h1>{review.title}</h1><p>转写已完成。可把初稿交给网页大模型校对，将完整结果填回右侧，再完成任务。</p>
    </div></header>
    <section className="panel">
      <p>1. 下载或复制初稿。2. 自行打开常用网页大模型，上传初稿并发送校对提示词。3. 粘贴或导入完整修改稿，检查后完成。</p>
      <div className="actions">
        <a className="button-link" href={api.manualDraftUrl(taskId)}>下载初稿 Markdown</a>
        <button onClick={() => void copy(review.original_text)}>复制完整初稿</button>
        <button onClick={() => void copy(review.prompt)}>复制校对提示词</button>
      </div>
      <details><summary>校对提示词</summary><p>{review.prompt}</p></details>
      <small>等待校对时不占用处理队列。保存修改稿后可以关闭页面，稍后继续。</small>
    </section>
    <div className="manual-review-grid">
      <section className="panel"><label>完整初稿 · {review.original_text.length} 字
        <textarea readOnly rows={24} value={review.original_text} />
      </label></section>
      <section className="panel"><label>修改后的完整正文 · {edited.length} 字
        <textarea rows={24} maxLength={2_000_000} disabled={busy} value={edited}
          onChange={event => setEdited(event.target.value)} placeholder="将网页大模型校对后的完整正文粘贴到这里，也可以手动修改。" />
      </label><label>导入修改稿（Markdown / TXT）
        <input type="file" accept=".md,.txt,text/plain,text/markdown" disabled={busy}
          onChange={event => { void importText(event.target.files?.[0]); event.target.value = ""; }} />
      </label></section>
    </div>
    <section className="panel"><div className="actions">
      <button className="primary" disabled={busy || !edited.trim()} onClick={() => void submit("edited")}>采用修改稿并完成</button>
      <button disabled={busy} onClick={() => void save()}>保存修改稿</button>
      <button disabled={busy} onClick={() => {
        if (edited.trim()) setConfirmation({ action: "original", text: "将直接使用初稿导出。当前填入的修改稿不会用于成品。" });
        else void submit("original");
      }}>使用初稿完成</button>
    </div><p role="status">{busy ? "正在处理…" : message ?? (edited !== saved ? "修改尚未保存。" : "")}</p></section>
    {confirmation && <ConfirmDialog title="确认采用的稿件" message={confirmation.text}
      confirmLabel={confirmation.action === "original" ? "使用初稿完成" : "确认修改并完成"}
      onCancel={() => setConfirmation(null)} onConfirm={() => void submit(confirmation.action, true)} />}
  </>}</StateView>;
}
