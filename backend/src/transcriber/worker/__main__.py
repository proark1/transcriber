"""Railway worker process entry point."""

from __future__ import annotations

import signal
from threading import Event

from transcriber.config import AppSettings
from transcriber.database import create_database_engine, create_session_factory
from transcriber.storage import BotoObjectStorage
from transcriber.whisper_engine import WhisperTranscriber
from transcriber.worker.runner import WorkerRunner


def main() -> None:
    settings = AppSettings()  # type: ignore[call-arg]
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    stopped = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    transcriber = WhisperTranscriber(
        model_name=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        download_root=settings.whisper_model_cache,
    )
    runner = WorkerRunner(
        settings,
        sessions,
        BotoObjectStorage(settings),
        transcriber,
    )
    try:
        runner.run_until_stopped(stopped)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
