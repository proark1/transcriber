"""Separate-connection heartbeats for long FFmpeg and Whisper operations."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from threading import Event, Thread


class LeaseHeartbeat(AbstractContextManager["LeaseHeartbeat"]):
    def __init__(
        self,
        refresh: Callable[[], None],
        *,
        interval_seconds: int,
    ) -> None:
        self._refresh = refresh
        self._interval_seconds = interval_seconds
        self._stopped = Event()
        self._thread: Thread | None = None

    def __enter__(self) -> LeaseHeartbeat:
        self._thread = Thread(target=self._run, daemon=True, name="worker-heartbeat")
        self._thread.start()
        return self

    def __exit__(self, *_error: object) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1, self._interval_seconds))

    def _run(self) -> None:
        while not self._stopped.wait(self._interval_seconds):
            try:
                self._refresh()
            except Exception:
                # The foreground completion update still verifies lease ownership.
                # A transient heartbeat failure must not kill inference mid-chunk.
                continue
