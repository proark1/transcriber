import { useState } from "react";

import { useAuth } from "../auth/AuthProvider.tsx";
import type { DeleteRecordingResponse, RecordingResponse } from "../api/contracts.ts";
import { StatusMessage } from "../components/StatusMessage.tsx";
import { AudioPlayer } from "./AudioPlayer.tsx";
import { DeleteRecordingDialog } from "./DeleteRecordingDialog.tsx";
import { ProcessingStatus } from "./ProcessingStatus.tsx";
import {
  formatDuration,
  formatRecordingDate,
  languageName,
  recordingStatusName,
} from "./recordingFormat.ts";
import { TranscriptView } from "./TranscriptView.tsx";

const FAILED_MESSAGES: Record<string, string> = {
  unsupported_media: "This file could not be read as supported audio.",
  duration_limit_exceeded: "This recording is longer than the 4-hour limit.",
  empty_audio: "No speech could be found in this recording.",
  transcription_failed: "The transcription service could not finish every part.",
  storage_unavailable: "Storage was temporarily unavailable.",
};

export function RecordingPage({
  recording,
  anotherRecordingActive,
  onRefresh,
  onRemoved,
}: {
  recording: RecordingResponse;
  anotherRecordingActive: boolean;
  onRefresh: () => void | Promise<void>;
  onRemoved: () => void | Promise<void>;
}) {
  const { api } = useAuth();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState(false);
  const [deleteError, setDeleteError] = useState(false);

  const processing = recording.status !== "completed" && recording.status !== "failed";

  async function retry() {
    setRetrying(true);
    setRetryError(false);
    try {
      await api.request<RecordingResponse>(`/api/recordings/${recording.id}/retry`, {
        method: "POST",
      });
      await onRefresh();
    } catch {
      setRetryError(true);
    } finally {
      setRetrying(false);
    }
  }

  async function remove() {
    setDeleteError(false);
    try {
      const response = await api.request<DeleteRecordingResponse | void>(
        `/api/recordings/${recording.id}`,
        { method: "DELETE" },
      );
      setDeleteOpen(false);
      if (response?.status === "deleting") await onRefresh();
      else await onRemoved();
    } catch {
      setDeleteError(true);
      setDeleteOpen(false);
    }
  }

  return (
    <div className="recording-page">
      <header className="recording-heading">
        <div>
          <p className="eyebrow">Saved recording</p>
          <h1>{recording.filename}</h1>
          <dl className="recording-facts">
            <div>
              <dt>Language</dt>
              <dd>{languageName(recording.language)}</dd>
            </div>
            <div>
              <dt>Added</dt>
              <dd>{formatRecordingDate(recording.createdAt)}</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>{formatDuration(recording.durationSeconds)}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{recordingStatusName(recording.status)}</dd>
            </div>
          </dl>
        </div>
        {recording.status !== "deleting" ? (
          <button className="delete-link" onClick={() => setDeleteOpen(true)}>
            Delete recording
          </button>
        ) : null}
      </header>

      {deleteError ? (
        <StatusMessage tone="error">
          Deletion could not finish yet. The recording is still safe; try again.
        </StatusMessage>
      ) : null}

      {processing ? <ProcessingStatus recording={recording} /> : null}

      {recording.status === "failed" ? (
        <section className="failed-card" aria-labelledby="failed-heading">
          <p className="mono-label">Needs attention</p>
          <h2 id="failed-heading">This transcription paused.</h2>
          <p>
            {FAILED_MESSAGES[recording.safeErrorCode ?? ""] ??
              "The recording could not be finished this time."}
          </p>
          <p>Any completed parts are saved and will not be transcribed again.</p>
          {retryError ? (
            <StatusMessage tone="error">Retry could not start. Wait a moment and try again.</StatusMessage>
          ) : null}
          <button
            className="button button--primary"
            onClick={() => void retry()}
            disabled={retrying || anotherRecordingActive}
          >
            {retrying ? "Starting retry..." : "Retry unfinished parts"}
          </button>
          {anotherRecordingActive ? (
            <p className="quiet-state">Finish the active recording before retrying this one.</p>
          ) : null}
        </section>
      ) : null}

      {recording.status === "completed" ? (
        <>
          {recording.hasPlayback ? <AudioPlayer recordingId={recording.id} /> : null}
          {recording.hasTranscript ? <TranscriptView recordingId={recording.id} /> : null}
        </>
      ) : null}

      <DeleteRecordingDialog
        filename={recording.filename}
        open={deleteOpen}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={remove}
      />
    </div>
  );
}
