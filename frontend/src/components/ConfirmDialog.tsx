import { useEffect, useRef } from "react";

export function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel }: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="confirm-dialog" aria-labelledby="confirm-title"
    aria-describedby="confirm-message" onCancel={onCancel}>
    <h2 id="confirm-title">{title}</h2>
    <p id="confirm-message">{message}</p>
    <div className="actions">
      <button autoFocus onClick={onCancel}>取消</button>
      <button className="danger-button" onClick={onConfirm}>{confirmLabel}</button>
    </div>
  </dialog>;
}
