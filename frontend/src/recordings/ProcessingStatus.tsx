import type { RecordingResponse, RecordingStatus } from "../api/contracts.ts";

const STAGES = ["Upload", "Check", "Prepare", "Transcribe", "Finish"] as const;

const STAGE_INDEX: Record<RecordingStatus, number> = {
  uploading: 0,
  queued: 1,
  validating: 1,
  normalizing: 2,
  chunking: 2,
  transcribing: 3,
  assembling: 4,
  completed: 5,
  failed: -1,
  deleting: -1,
};

export function statusLabel(recording: RecordingResponse): string {
  switch (recording.status) {
    case "uploading":
      return "Uploading";
    case "queued":
      return "Waiting to start";
    case "validating":
      return "Checking the audio";
    case "normalizing":
      return "Preparing the sound";
    case "chunking":
      return "Saving restart-safe parts";
    case "transcribing":
      return recording.totalChunks > 0
        ? `Transcribing ${recording.completedChunks + 1} of ${recording.totalChunks}`
        : "Transcribing";
    case "assembling":
      return "Making the text readable";
    case "completed":
      return "Transcript ready";
    case "failed":
      return "Needs attention";
    case "deleting":
      return "Deleting safely";
  }
}

export function ProcessingStatus({ recording }: { recording: RecordingResponse }) {
  const currentStage = STAGE_INDEX[recording.status];
  return (
    <section className="processing-card" aria-labelledby="processing-heading">
      <div className="processing-spine" aria-hidden="true">
        {STAGES.map((stage, index) => (
          <span
            key={stage}
            className={
              index < currentStage
                ? "is-complete"
                : index === currentStage
                  ? "is-active"
                  : ""
            }
          />
        ))}
      </div>
      <div className="processing-content">
        <p className="mono-label">In progress</p>
        <h2 id="processing-heading">{statusLabel(recording)}</h2>
        {recording.status === "transcribing" && recording.totalChunks > 0 ? (
          <div
            className="chunk-progress"
            role="progressbar"
            aria-label="Transcription parts completed"
            aria-valuemin={0}
            aria-valuemax={recording.totalChunks}
            aria-valuenow={recording.completedChunks}
          >
            <span
              style={{ width: `${(recording.completedChunks / recording.totalChunks) * 100}%` }}
            />
          </div>
        ) : null}
        <p className="processing-note">
          You can close this page. Processing continues safely on Railway, and completed parts
          will not be repeated.
        </p>
        <ol className="stage-list">
          {STAGES.map((stage, index) => (
            <li key={stage} className={index <= currentStage ? "is-reached" : ""}>
              <span>{String(index + 1).padStart(2, "0")}</span> {stage}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
