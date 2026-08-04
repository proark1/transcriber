import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth/AuthProvider.tsx";
import type { RecordingResponse, RecordingStatus } from "../api/contracts.ts";
import { NewTranscription } from "../recordings/NewTranscription.tsx";

const ACTIVE_STATUSES = new Set<RecordingStatus>([
  "uploading",
  "queued",
  "validating",
  "normalizing",
  "chunking",
  "transcribing",
  "assembling",
  "deleting",
]);

export function AppShell() {
  const { username, logout, api } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);
  const [recordings, setRecordings] = useState<RecordingResponse[]>([]);
  const [loadError, setLoadError] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setRecordings(await api.request<RecordingResponse[]>("/api/recordings"));
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  const active = useMemo(
    () => recordings.find((recording) => ACTIVE_STATUSES.has(recording.status)) ?? null,
    [recordings],
  );
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void refresh(), 4_000);
    return () => window.clearInterval(timer);
  }, [active, refresh]);

  async function signOut() {
    setLoggingOut(true);
    await logout();
  }

  return (
    <div className="app-frame">
      <header className="app-header">
        <a className="wordmark" href="/" aria-label="Transcriber home">
          <span className="brand-tile" aria-hidden="true">
            T
          </span>
          <span>Transcriber</span>
        </a>
        <div className="account-menu">
          <span>
            <i aria-hidden="true" /> {username}
          </span>
          <button className="text-button" onClick={signOut} disabled={loggingOut}>
            {loggingOut ? "Signing out..." : "Sign out"}
          </button>
        </div>
      </header>
      <main className="workspace-shell">
        {loadError ? (
          <button className="load-warning" onClick={() => void refresh()}>
            History could not refresh. Try again.
          </button>
        ) : null}
        <NewTranscription
          activeRecording={active}
          onQueued={async () => {
            await refresh();
          }}
        />
      </main>
    </div>
  );
}
