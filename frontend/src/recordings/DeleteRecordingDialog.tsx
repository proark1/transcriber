import { useEffect, useState } from "react";

export function DeleteRecordingDialog({
  filename,
  open,
  onCancel,
  onConfirm,
}: {
  filename: string;
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
}) {
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (!open) setDeleting(false);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && !deleting) onCancel();
    }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [deleting, onCancel, open]);

  if (!open) return null;

  async function confirm() {
    setDeleting(true);
    try {
      await onConfirm();
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={() => !deleting && onCancel()}>
      <section
        className="delete-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-heading"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <p className="mono-label">Permanent deletion</p>
        <h2 id="delete-heading">Delete this recording?</h2>
        <p>
          <strong>{filename}</strong> and its transcript will be permanently removed. This cannot
          be undone.
        </p>
        <div className="dialog-actions">
          <button className="button button--secondary" onClick={onCancel} disabled={deleting}>
            Keep recording
          </button>
          <button className="button button--danger" onClick={() => void confirm()} disabled={deleting}>
            {deleting ? "Deleting..." : "Delete permanently"}
          </button>
        </div>
      </section>
    </div>
  );
}
