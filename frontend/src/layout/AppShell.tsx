import { type ReactNode, useState } from "react";

import { useAuth } from "../auth/AuthProvider.tsx";

export function AppShell({ children }: { children?: ReactNode }) {
  const { username, logout } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);

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
            {loggingOut ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </header>
      <main className="workspace-shell">
        {children ?? (
          <section className="empty-workspace">
            <p className="eyebrow">Workspace ready</p>
            <h1>Choose a recording or start a new transcription.</h1>
          </section>
        )}
      </main>
    </div>
  );
}
