import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../auth/AuthProvider.tsx";
import type { PlaybackResponse } from "../api/contracts.ts";

export function AudioPlayer({ recordingId }: { recordingId: string }) {
  const { api } = useAuth();
  const [source, setSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const refreshing = useRef(false);
  const automaticRetries = useRef(0);

  const refresh = useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    setFailed(false);
    try {
      const playback = await api.request<PlaybackResponse>(
        `/api/recordings/${recordingId}/playback`,
      );
      setSource(playback.url);
    } catch {
      setFailed(true);
    } finally {
      refreshing.current = false;
    }
  }, [api, recordingId]);

  useEffect(() => {
    automaticRetries.current = 0;
    setSource(null);
    void refresh();
  }, [refresh]);

  function recoverExpiredSource() {
    if (automaticRetries.current < 1) {
      automaticRetries.current += 1;
      void refresh();
      return;
    }
    setSource(null);
    setFailed(true);
  }

  return (
    <section className="audio-card" aria-labelledby="audio-heading">
      <div>
        <p className="mono-label">Audio</p>
        <h2 id="audio-heading">Listen to the recording</h2>
      </div>
      {source ? (
        <audio controls preload="metadata" src={source} onError={recoverExpiredSource}>
          Your browser cannot play this audio.
        </audio>
      ) : failed ? (
        <button
          className="button button--secondary"
          onClick={() => {
            automaticRetries.current = 0;
            void refresh();
          }}
        >
          Try loading audio again
        </button>
      ) : (
        <p className="quiet-state" role="status">
          Loading audio...
        </p>
      )}
    </section>
  );
}
