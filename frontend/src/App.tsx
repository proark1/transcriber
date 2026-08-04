const CHUNK_MARKERS = ["complete", "complete", "active", "waiting", "waiting"] as const;

export function App() {
  return (
    <main className="launch-shell">
      <header className="launch-header">
        <a className="wordmark" href="/" aria-label="Transcriber home">
          <span className="wordmark-mark" aria-hidden="true">
            T
          </span>
          <span>Transcriber</span>
        </a>
        <span className="privacy-label">Private audio workspace</span>
      </header>

      <section className="launch-panel" aria-labelledby="launch-title">
        <div className="tape-spine" aria-label="Example chunk progress">
          {CHUNK_MARKERS.map((state, index) => (
            <span className={`tape-segment tape-segment--${state}`} key={`${state}-${index}`} />
          ))}
        </div>

        <div className="launch-copy">
          <p className="eyebrow">Railway-ready · Restart-safe</p>
          <h1 id="launch-title">Long recordings in. Clean text out.</h1>
          <p className="launch-lede">
            A focused transcription workspace for iPhone voice notes, interviews, and
            multi-hour audio—built to keep completed work safe through every restart.
          </p>
        </div>

        <dl className="launch-facts">
          <div>
            <dt>Languages</dt>
            <dd>English · German · Turkish</dd>
          </div>
          <div>
            <dt>Output</dt>
            <dd>Readable text and TXT</dd>
          </div>
          <div>
            <dt>Storage</dt>
            <dd>Private and permanent</dd>
          </div>
        </dl>

        <p className="build-status">
          <span aria-hidden="true" /> Application foundation ready
        </p>
      </section>
    </main>
  );
}
