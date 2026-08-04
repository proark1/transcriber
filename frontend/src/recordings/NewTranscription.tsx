import { type DragEvent, useId, useRef, useState } from "react";

import { useAuth } from "../auth/AuthProvider.tsx";
import type { Language, RecordingResponse } from "../api/contracts.ts";
import { StatusMessage } from "../components/StatusMessage.tsx";
import { ProcessingStatus } from "./ProcessingStatus.tsx";
import { useMultipartUpload } from "./useMultipartUpload.ts";

const MAX_BYTES = 5_000_000_000;
const ACCEPT = ".m4a,.mp3,.wav,.aac,.flac,.ogg,.opus,.mp4,audio/*,video/mp4";

export function NewTranscription({
  activeRecording,
  onQueued,
}: {
  activeRecording: RecordingResponse | null;
  onQueued: (recordingId: string) => void | Promise<void>;
}) {
  const { api } = useAuth();
  const upload = useMultipartUpload({ api, onQueued });
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState<Language>("en");
  const [dragging, setDragging] = useState(false);
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);

  if (activeRecording && (activeRecording.status !== "uploading" || !upload.state.pending)) {
    return <ProcessingStatus recording={activeRecording} />;
  }

  function choose(nextFile: File | undefined) {
    if (!nextFile) return;
    if (upload.state.pending) {
      void upload.resume(nextFile);
      return;
    }
    setFile(nextFile);
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  const busy = upload.state.phase === "uploading" || upload.state.phase === "completing";
  const invalidSize = file ? file.size <= 0 || file.size > MAX_BYTES : false;

  return (
    <section className="new-transcription" aria-labelledby="new-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">New transcription</p>
          <h1 id="new-heading">Turn a recording into clean text.</h1>
        </div>
        <p>One recording at a time &middot; Up to 4 hours</p>
      </div>

      {upload.state.pending && !busy ? (
        <div className="resume-banner">
          <div>
            <p className="mono-label">Saved upload found</p>
            <h2>Continue {upload.state.pending.filename}</h2>
            <p>Choose the same file. Only missing parts will upload again.</p>
          </div>
          <label className="button button--secondary" htmlFor={inputId}>
            Choose file to continue
          </label>
        </div>
      ) : null}

      <div className="upload-layout">
        <div
          className={`drop-zone ${dragging ? "is-dragging" : ""}`}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={drop}
        >
          <input
            ref={input}
            id={inputId}
            className="visually-hidden"
            type="file"
            accept={ACCEPT}
            onChange={(event) => choose(event.target.files?.[0])}
          />
          <span className="drop-mark" aria-hidden="true">
            +
          </span>
          <h2>{file ? file.name : "Drop your audio here"}</h2>
          <p>
            {file
              ? formatBytes(file.size)
              : "Or choose a file from this computer or your iPhone"}
          </p>
          <label className="button button--secondary" htmlFor={inputId}>
            {file ? "Choose a different file" : "Choose audio file"}
          </label>
          <p className="format-line">M4A &middot; MP3 &middot; WAV &middot; AAC &middot; FLAC &middot; OGG &middot; OPUS &middot; MP4</p>
        </div>

        <div className="upload-options">
          <div>
            <label htmlFor="language">Spoken language</label>
            <select
              className="field"
              id="language"
              value={language}
              onChange={(event) => setLanguage(event.target.value as Language)}
              disabled={busy || Boolean(upload.state.pending)}
            >
              <option value="en">English</option>
              <option value="de">German</option>
              <option value="tr">Turkish</option>
            </select>
            <p>Choosing the language improves names, punctuation, and accuracy.</p>
          </div>

          {invalidSize ? (
            <StatusMessage tone="error">Choose a non-empty file smaller than 5 GB.</StatusMessage>
          ) : null}
          {upload.state.error ? (
            <StatusMessage tone="error">{upload.state.error}</StatusMessage>
          ) : null}
          {busy ? (
            <div className="upload-progress" aria-live="polite">
              <div>
                <span>{upload.state.phase === "completing" ? "Finishing" : "Uploading"}</span>
                <strong>{Math.round(upload.state.progress * 100)}%</strong>
              </div>
              <div
                className="progress-track"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(upload.state.progress * 100)}
              >
                <span style={{ width: `${upload.state.progress * 100}%` }} />
              </div>
              <button className="text-button" onClick={() => void upload.cancel()}>
                Cancel upload
              </button>
            </div>
          ) : (
            <button
              className="button button--primary button--wide"
              disabled={!file || invalidSize || Boolean(upload.state.pending)}
              onClick={() => file && void upload.start(file, language)}
            >
              Start transcription
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function formatBytes(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(1)} KB`;
  return `${bytes} bytes`;
}
