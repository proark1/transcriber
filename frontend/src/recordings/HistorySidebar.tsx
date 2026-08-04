import type { RecordingResponse } from "../api/contracts.ts";
import {
  formatRecordingDate,
  languageName,
  recordingStatusName,
} from "./recordingFormat.ts";

export function HistorySidebar({
  recordings,
  selectedId,
  activeExists,
  onSelect,
  onNew,
}: {
  recordings: RecordingResponse[];
  selectedId: string | null;
  activeExists: boolean;
  onSelect: (recordingId: string) => void;
  onNew: () => void;
}) {
  return (
    <aside className="history-sidebar" aria-label="Recording history">
      <div className="history-topline">
        <p className="mono-label">History</p>
        <span>{recordings.length}</span>
      </div>
      <button
        className={`new-recording-button ${selectedId === null ? "is-selected" : ""}`}
        onClick={onNew}
        disabled={activeExists}
      >
        <span aria-hidden="true">+</span>
        New transcription
      </button>

      <label className="history-mobile-label" htmlFor="history-mobile-select">
        Open a recording
      </label>
      <select
        id="history-mobile-select"
        className="field history-mobile-select"
        value={selectedId ?? "new"}
        onChange={(event) =>
          event.target.value === "new" ? onNew() : onSelect(event.target.value)
        }
      >
        <option value="new" disabled={activeExists}>
          New transcription
        </option>
        {recordings.map((recording) => (
          <option key={recording.id} value={recording.id}>
            {recording.filename} - {recordingStatusName(recording.status)}
          </option>
        ))}
      </select>

      <nav className="history-list" aria-label="Saved recordings">
        {recordings.length === 0 ? (
          <p className="history-empty">Finished recordings will stay here until you delete them.</p>
        ) : (
          recordings.map((recording) => {
            const status = recordingStatusName(recording.status);
            return (
              <button
                key={recording.id}
                className={`history-item ${selectedId === recording.id ? "is-selected" : ""}`}
                onClick={() => onSelect(recording.id)}
                aria-current={selectedId === recording.id ? "page" : undefined}
              >
                <span className="history-filename">{recording.filename}</span>
                <span className="history-meta">
                  {languageName(recording.language)} / {formatRecordingDate(recording.createdAt)}
                </span>
                <span className={`status-chip status-chip--${recording.status}`}>{status}</span>
              </button>
            );
          })
        )}
      </nav>
    </aside>
  );
}
