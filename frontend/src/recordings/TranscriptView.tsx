import { useEffect, useRef, useState } from "react";

import { useAuth } from "../auth/AuthProvider.tsx";
import { StatusMessage } from "../components/StatusMessage.tsx";

type CopyState = "idle" | "copied" | "failed";

export function TranscriptView({ recordingId }: { recordingId: string }) {
  const { api } = useAuth();
  const [transcript, setTranscript] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const copyReset = useRef<number | null>(null);

  useEffect(() => {
    let active = true;
    setTranscript(null);
    setLoadFailed(false);
    api
      .request<string>(`/api/recordings/${recordingId}/transcript`)
      .then((text) => {
        if (active) setTranscript(text);
      })
      .catch(() => {
        if (active) setLoadFailed(true);
      });
    return () => {
      active = false;
      if (copyReset.current !== null) window.clearTimeout(copyReset.current);
    };
  }, [api, recordingId]);

  async function copyTranscript() {
    if (transcript === null) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(transcript);
      } else {
        fallbackCopy(transcript);
      }
      setCopyState("copied");
    } catch {
      try {
        fallbackCopy(transcript);
        setCopyState("copied");
      } catch {
        setCopyState("failed");
      }
    }
    if (copyReset.current !== null) window.clearTimeout(copyReset.current);
    copyReset.current = window.setTimeout(() => setCopyState("idle"), 3_000);
  }

  return (
    <section className="transcript-section" aria-labelledby="transcript-heading">
      <div className="transcript-toolbar">
        <div>
          <p className="mono-label">Transcript</p>
          <h2 id="transcript-heading">Clean, readable text</h2>
        </div>
        <div className="transcript-actions">
          <button
            className="button button--secondary"
            onClick={() => void copyTranscript()}
            disabled={transcript === null}
          >
            {copyState === "copied" ? "Copied" : "Copy text"}
          </button>
          <a
            className="button button--primary"
            href={`/api/recordings/${recordingId}/transcript.txt`}
          >
            Download TXT
          </a>
        </div>
      </div>
      <div aria-live="polite">
        {copyState === "copied" ? (
          <StatusMessage tone="success">Transcript copied.</StatusMessage>
        ) : null}
        {copyState === "failed" ? (
          <StatusMessage tone="error">Copy was blocked. Select the text below instead.</StatusMessage>
        ) : null}
      </div>
      {loadFailed ? (
        <StatusMessage tone="error">The transcript could not be loaded. Refresh and try again.</StatusMessage>
      ) : transcript === null ? (
        <p className="quiet-state" role="status">
          Loading transcript...
        </p>
      ) : (
        <article className="transcript-text">{transcript}</article>
      )}
    </section>
  );
}

function fallbackCopy(text: string): void {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand?.("copy") ?? false;
  textarea.remove();
  if (!copied) throw new Error("Copy was blocked");
}
