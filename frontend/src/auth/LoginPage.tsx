import { type FormEvent, useState } from "react";

import { ApiError } from "../api/client.ts";
import { StatusMessage } from "../components/StatusMessage.tsx";
import { useAuth } from "./AuthProvider.tsx";

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [pin, setPin] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(username.trim(), pin);
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 429) {
        const minutes = Math.max(1, Math.ceil((caught.retryAfterSeconds ?? 900) / 60));
        setError(`Too many attempts. Please wait about ${minutes} minutes.`);
      } else {
        setError("That username or PIN wasn’t accepted.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-intro" aria-labelledby="login-title">
        <a className="wordmark" href="/" aria-label="Transcriber home">
          <span className="brand-tile" aria-hidden="true">
            T
          </span>
          <span>Transcriber</span>
        </a>
        <p className="eyebrow">Private audio workspace</p>
        <h1 id="login-title">Your recordings stay yours.</h1>
        <p className="login-lede">
          Upload an iPhone voice note or a multi-hour recording. The app keeps each finished
          part safe and turns it into clean, readable text.
        </p>
        <div className="language-line" aria-label="Available languages">
          <span>EN</span>
          <span>DE</span>
          <span>TR</span>
        </div>
      </section>

      <section className="login-card" aria-label="Sign in">
        <div>
          <p className="mono-label">Owner access</p>
          <h2>Welcome back</h2>
          <p>Use the username and PIN stored in Railway.</p>
        </div>
        <form onSubmit={submit}>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            autoFocus
          />
          <label htmlFor="pin">PIN</label>
          <input
            id="pin"
            name="pin"
            type="password"
            inputMode="numeric"
            pattern="[0-9]{6,12}"
            minLength={6}
            maxLength={12}
            autoComplete="current-password"
            value={pin}
            onChange={(event) => setPin(event.target.value.replace(/\D/g, ""))}
            required
          />
          {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}
          <button className="button button--primary button--wide" disabled={submitting}>
            {submitting ? "Checking…" : "Open workspace"}
          </button>
        </form>
        <p className="privacy-note">
          <span aria-hidden="true">●</span> Private by default · Seven-day session
        </p>
      </section>
    </main>
  );
}
