import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth/AuthProvider.tsx";
import type { RecordingResponse, RecordingStatus } from "../api/contracts.ts";
import { HistorySidebar } from "../recordings/HistorySidebar.tsx";
import { NewTranscription } from "../recordings/NewTranscription.tsx";
import { RecordingPage } from "../recordings/RecordingPage.tsx";

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
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const nextRecordings = await api.request<RecordingResponse[]>("/api/recordings");
      setRecordings(nextRecordings);
      setSelectedId((current) => {
        if (current && nextRecordings.some((recording) => recording.id === current)) {
          return current;
        }
        return (
          nextRecordings.find((recording) => ACTIVE_STATUSES.has(recording.status))?.id ?? null
        );
      });
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
  const selected = useMemo(
    () => recordings.find((recording) => recording.id === selectedId) ?? null,
    [recordings, selectedId],
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
        <div className="workspace-layout">
          <HistorySidebar
            recordings={recordings}
            selectedId={selectedId}
            activeExists={active !== null}
            onSelect={setSelectedId}
            onNew={() => setSelectedId(null)}
          />
          <div className="workspace-main">
            {selected ? (
              <RecordingPage
                recording={selected}
                anotherRecordingActive={active !== null && active.id !== selected.id}
                onRefresh={refresh}
                onRemoved={async () => {
                  setSelectedId(null);
                  await refresh();
                }}
              />
            ) : (
              <NewTranscription
                activeRecording={active}
                onQueued={async (recordingId) => {
                  setSelectedId(recordingId);
                  await refresh();
                }}
              />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
