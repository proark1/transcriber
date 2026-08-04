"""One-at-a-time worker orchestration with durable leases and checkpoints."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from transcriber.assembly import AssemblyChunk, AssemblyError, assemble_transcript
from transcriber.config import AppSettings
from transcriber.media import MediaError
from transcriber.models import Recording, TranscriptionChunk
from transcriber.storage import ObjectStorage, StorageError
from transcriber.whisper_engine import (
    Transcriber,
    TranscriptionError,
    clean_chunk_text,
    segments_from_json,
    segments_to_json,
)
from transcriber.worker.cleanup import remove_working_chunks
from transcriber.worker.leases import LeaseHeartbeat
from transcriber.worker.preparation import PreparationService
from transcriber.worker.repository import (
    LeaseLost,
    WorkClaim,
    WorkerRepository,
    WorkKind,
)


class Preparer(Protocol):
    def prepare(self, recording_id: UUID) -> None: ...


class WorkerRunner:
    def __init__(
        self,
        settings: AppSettings,
        session_factory: sessionmaker[Session],
        storage: ObjectStorage,
        transcriber: Transcriber,
        *,
        preparation: Preparer | None = None,
        worker_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._storage = storage
        self._transcriber = transcriber
        self._preparation = preparation or PreparationService(settings, session_factory, storage)
        self._worker_id = worker_id or uuid4().hex
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_one(self) -> bool:
        claim = self._claim_next()
        if claim is None:
            return self._cleanup_one()
        heartbeat = LeaseHeartbeat(
            lambda: self._heartbeat(claim),
            interval_seconds=self._settings.worker_heartbeat_seconds,
        )
        try:
            with heartbeat:
                if claim.kind is WorkKind.PREPARATION:
                    self._run_preparation(claim)
                elif claim.kind is WorkKind.CHUNK:
                    self._run_chunk(claim)
                else:
                    self._run_assembly(claim)
        except LeaseLost:
            return True
        return True

    def run_until_stopped(self, stopped: Event) -> None:
        while not stopped.is_set():
            if not self.run_one():
                stopped.wait(self._settings.worker_poll_seconds)

    def _claim_next(self) -> WorkClaim | None:
        with self._sessions.begin() as database:
            return self._repository(database).claim_next(self._worker_id, now=self._clock())

    def _heartbeat(self, claim: WorkClaim) -> None:
        with self._sessions.begin() as database:
            self._repository(database).heartbeat(claim, now=self._clock())

    def _run_preparation(self, claim: WorkClaim) -> None:
        try:
            self._preparation.prepare(claim.recording_id)
            with self._sessions.begin() as database:
                self._repository(database).finish_preparation(claim)
        except LeaseLost:
            raise
        except Exception as error:
            code = error.code if isinstance(error, MediaError) else "preparation_failed"
            with self._sessions.begin() as database:
                self._repository(database).fail_preparation(claim, code)

    def _run_chunk(self, claim: WorkClaim) -> None:
        if claim.chunk_id is None:
            raise LeaseLost("A chunk claim is incomplete.")
        with self._sessions() as database:
            chunk = database.get(TranscriptionChunk, claim.chunk_id)
            recording = database.get(Recording, claim.recording_id)
            if chunk is None or recording is None:
                raise LeaseLost("The claimed chunk no longer exists.")
            object_key = chunk.object_key
            language = recording.language
        try:
            self._settings.worker_scratch_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(
                prefix=f"chunk-{claim.chunk_index}-",
                dir=self._settings.worker_scratch_dir,
            ) as temporary_directory:
                audio_path = Path(temporary_directory) / "chunk.flac"
                self._storage.download_file(object_key, audio_path)
                segments = self._transcriber.transcribe(audio_path, language)
            with self._sessions.begin() as database:
                self._repository(database).complete_chunk(
                    claim,
                    internal_segments=segments_to_json(segments),
                    clean_text=clean_chunk_text(segments),
                    now=self._clock(),
                )
        except LeaseLost:
            raise
        except Exception as error:
            code = _chunk_error_code(error)
            with self._sessions.begin() as database:
                self._repository(database).fail_chunk(claim, code, now=self._clock())

    def _run_assembly(self, claim: WorkClaim) -> None:
        object_keys: list[str]
        try:
            with self._sessions() as database:
                _recording, chunks = self._repository(database).assembly_chunks(claim)
                assembly_chunks = [
                    AssemblyChunk(
                        chunk_index=chunk.chunk_index,
                        core_start_seconds=chunk.core_start_seconds,
                        core_end_seconds=chunk.core_end_seconds,
                        audio_start_seconds=chunk.audio_start_seconds,
                        segments=tuple(segments_from_json(chunk.internal_segments)),
                    )
                    for chunk in chunks
                ]
                object_keys = [chunk.object_key for chunk in chunks]
                database.commit()
            transcript = assemble_transcript(
                assembly_chunks,
                overlap_window_seconds=self._settings.chunk_overlap_seconds,
            )
            with self._sessions.begin() as database:
                self._repository(database).complete_assembly(claim, transcript, now=self._clock())
        except LeaseLost:
            raise
        except Exception as error:
            code = error.code if isinstance(error, AssemblyError) else "assembly_failed"
            with self._sessions.begin() as database:
                self._repository(database).fail_assembly(claim, code)
            return
        if remove_working_chunks(self._storage, object_keys):
            try:
                with self._sessions.begin() as database:
                    self._repository(database).mark_chunks_cleaned(
                        claim.recording_id, now=self._clock()
                    )
            except Exception:
                # The completed transcript is durable. Idle cleanup will retry this
                # idempotent deletion/checkpoint path after a transient database error.
                return

    def _repository(self, database: Session) -> WorkerRepository:
        return WorkerRepository(database, lease_seconds=self._settings.worker_lease_seconds)

    def _cleanup_one(self) -> bool:
        with self._sessions.begin() as database:
            candidate = self._repository(database).cleanup_candidate()
        if candidate is None:
            return False
        recording_id, object_keys = candidate
        if not remove_working_chunks(self._storage, object_keys):
            return False
        with self._sessions.begin() as database:
            self._repository(database).mark_chunks_cleaned(recording_id, now=self._clock())
        return True


def _chunk_error_code(error: Exception) -> str:
    if isinstance(error, TranscriptionError):
        return error.code
    if isinstance(error, StorageError):
        return "chunk_download_failed"
    return "transcription_failed"
