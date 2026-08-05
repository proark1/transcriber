import { type FormEvent, useState } from "react";

import { ApiError } from "../api/client.ts";
import { StatusMessage } from "../components/StatusMessage.tsx";
import { useAuth } from "./AuthProvider.tsx";

const PIN_PATTERN = /^[0-9]{6,12}$/;

const ACCOUNT_ERRORS: Record<string, string> = {
  invalid_username: "Use 3–32 letters or numbers. You may also use ., _ or -.",
  username_unavailable: "That username is unavailable.",
  invalid_pin: "Use a 6–12 digit PIN.",
  incorrect_pin: "That PIN is incorrect for this username.",
};

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [pin, setPin] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!PIN_PATTERN.test(pin)) {
      setError("Use a 6–12 digit PIN.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await login(username.trim(), pin);
    } catch (caught: unknown) {
      if (caught instanceof ApiError && caught.status === 429) {
        const minutes = Math.max(1, Math.ceil((caught.retryAfterSeconds ?? 900) / 60));
        setError(`Too many attempts. Please wait about ${minutes} minutes.`);
      } else if (caught instanceof ApiError) {
        setError(
          ACCOUNT_ERRORS[caught.code] ?? "The account could not be opened. Retrying is safe.",
        );
      } else {
        setError("The account could not be opened. Retrying is safe.");
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
        <p className="eyebrow">Private audio transcription</p>
        <h1 id="login-title">Transcribe any audio file into clear text.</h1>
        <p className="login-lede">
          Bring a recording from your phone, computer, messaging app, or dedicated recorder. Even
          multi-hour files are processed in restart-safe parts and turned into clean, readable text.
        </p>
        <div className="format-line" aria-label="Supported audio formats">
          {['M4A', 'MP3', 'WAV', 'AAC', 'FLAC', 'OGG', 'Opus', 'MP4'].map((format) => (
            <span key={format}>{format}</span>
          ))}
        </div>
        <div className="language-line" aria-label="Available languages">
          <span>EN</span>
          <span>DE</span>
          <span>TR</span>
        </div>
      </section>

      <section className="login-card" aria-label="Sign in or create an account">
        <div>
          <p className="mono-label">Private account</p>
          <h2>Sign in or create an account.</h2>
          <p>Enter a new username to create a private account automatically.</p>
        </div>
        <form onSubmit={submit}>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
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
            maxLength={12}
            autoComplete="current-password"
            value={pin}
            onChange={(event) => setPin(event.target.value.replace(/\D/g, "").slice(0, 12))}
            required
          />
          {error ? <StatusMessage tone="error">{error}</StatusMessage> : null}
          <button className="button button--primary button--wide" disabled={submitting}>
            {submitting ? "Opening…" : "Continue"}
          </button>
        </form>
        <p className="privacy-note">
          <span aria-hidden="true">●</span> Private by account · Seven-day session
        </p>
      </section>
    </main>
  );
}
