"""Idempotent media validation, playback derivation, and chunk preparation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from transcriber.config import AppSettings
from transcriber.media import (
    AudioProbe,
    ChunkPlan,
    MediaError,
    MediaToolkit,
    Silence,
    plan_chunks,
    validate_local_size,
)
from transcriber.models import (
    ChunkStatus,
    Recording,
    RecordingStatus,
    TranscriptionChunk,
)
from transcriber.storage import ObjectStorage, StorageError


class MediaProcessor(Protocol):
    def probe(self, source: Path) -> AudioProbe: ...

    def normalize(self, source: Path, output: Path) -> None: ...

    def create_playback(self, source: Path, output: Path) -> None: ...

    def detect_silences(self, normalized: Path, duration_seconds: float) -> list[Silence]: ...

    def render_chunk(self, normalized: Path, output: Path, plan: ChunkPlan) -> None: ...


@dataclass(frozen=True)
class RecordingSource:
    id: UUID
    expected_bytes: int
    original_object_key: str


class PreparationService:
    """Prepare one recording so every expensive chunk can resume independently."""

    def __init__(
        self,
        settings: AppSettings,
        session_factory: sessionmaker[Session],
        storage: ObjectStorage,
        media: MediaProcessor | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._storage = storage
        self._media = media or MediaToolkit(
            ffmpeg_path=settings.ffmpeg_path,
            ffprobe_path=settings.ffprobe_path,
        )

    def prepare(self, recording_id: UUID) -> None:
        source = self._load_source(recording_id)
        if source is None:
            return
        self._settings.worker_scratch_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=f"recording-{recording_id}-",
            dir=self._settings.worker_scratch_dir,
        ) as temporary_directory:
            workspace = Path(temporary_directory)
            original = workspace / "original"
            normalized = workspace / "normalized.flac"
            playback = workspace / "playback.m4a"
            chunk_output = workspace / "chunk.flac"

            try:
                self._storage.download_file(source.original_object_key, original)
            except StorageError as error:
                raise MediaError("media_download_failed") from error
            validate_local_size(original, source.expected_bytes)
            probe = self._media.probe(original)
            self._store_probe(recording_id, probe)

            self._media.normalize(original, normalized)
            self._ensure_playback(recording_id, original, playback)
            silences = self._media.detect_silences(normalized, probe.duration_seconds)
            plans = plan_chunks(
                probe.duration_seconds,
                silences,
                core_seconds=self._settings.chunk_core_seconds,
                boundary_search_seconds=self._settings.chunk_boundary_search_seconds,
                overlap_seconds=self._settings.chunk_overlap_seconds,
            )
            self._persist_chunk_plan(recording_id, plans)
            for plan in plans:
                self._ensure_chunk(recording_id, normalized, chunk_output, plan)
            self._mark_ready_for_transcription(recording_id, len(plans))

    def _load_source(self, recording_id: UUID) -> RecordingSource | None:
        with self._sessions() as database:
            recording = database.get(Recording, recording_id)
            if recording is None:
                raise MediaError("recording_missing")
            if recording.status is RecordingStatus.TRANSCRIBING and recording.total_chunks > 0:
                return None
            if recording.status not in {
                RecordingStatus.QUEUED,
                RecordingStatus.VALIDATING,
                RecordingStatus.NORMALIZING,
                RecordingStatus.CHUNKING,
            }:
                raise MediaError("recording_state_conflict")
            source = RecordingSource(
                id=recording.id,
                expected_bytes=recording.expected_bytes,
                original_object_key=recording.original_object_key,
            )
            database.commit()
            return source

    def _store_probe(self, recording_id: UUID, probe: AudioProbe) -> None:
        with self._sessions.begin() as database:
            recording = _recording(database, recording_id)
            recording.duration_seconds = probe.duration_seconds
            recording.container = probe.container
            recording.audio_codec = probe.audio_codec
            recording.status = RecordingStatus.NORMALIZING
            recording.safe_error_code = None

    def _ensure_playback(self, recording_id: UUID, original: Path, playback: Path) -> None:
        self._require_preparation_allowed(recording_id)
        object_key = f"recordings/{recording_id}/playback/audio.m4a"
        try:
            metadata = self._storage.head_object(object_key)
        except StorageError as error:
            raise MediaError("media_storage_failed") from error
        if metadata is None or metadata.size_bytes <= 0:
            self._media.create_playback(original, playback)
            self._require_preparation_allowed(recording_id)
            try:
                self._storage.upload_file(object_key, playback, "audio/mp4")
                metadata = self._storage.head_object(object_key)
            except StorageError as error:
                raise MediaError("media_playback_upload_failed") from error
            if metadata is None or metadata.size_bytes <= 0:
                raise MediaError("media_playback_upload_failed")
        with self._sessions.begin() as database:
            recording = _recording(database, recording_id)
            recording.playback_object_key = object_key
            recording.status = RecordingStatus.CHUNKING

    def _persist_chunk_plan(self, recording_id: UUID, plans: list[ChunkPlan]) -> None:
        with self._sessions.begin() as database:
            recording = _recording(database, recording_id)
            existing = list(
                database.scalars(
                    select(TranscriptionChunk)
                    .where(TranscriptionChunk.recording_id == recording_id)
                    .order_by(TranscriptionChunk.chunk_index)
                )
            )
            if existing:
                if len(existing) != len(plans) or any(
                    not _chunk_matches_plan(chunk, plan)
                    for chunk, plan in zip(existing, plans, strict=True)
                ):
                    raise MediaError("chunk_plan_conflict")
            else:
                database.add_all(
                    [
                        TranscriptionChunk(
                            recording_id=recording_id,
                            chunk_index=plan.chunk_index,
                            core_start_seconds=plan.core_start_seconds,
                            core_end_seconds=plan.core_end_seconds,
                            audio_start_seconds=plan.audio_start_seconds,
                            audio_end_seconds=plan.audio_end_seconds,
                            object_key=_chunk_object_key(recording_id, plan.chunk_index),
                            status=ChunkStatus.PENDING,
                        )
                        for plan in plans
                    ]
                )
            recording.total_chunks = len(plans)
            recording.status = RecordingStatus.CHUNKING

    def _ensure_chunk(
        self,
        recording_id: UUID,
        normalized: Path,
        chunk_output: Path,
        plan: ChunkPlan,
    ) -> None:
        self._require_preparation_allowed(recording_id)
        object_key = _chunk_object_key(recording_id, plan.chunk_index)
        with self._sessions() as database:
            recorded_size = database.scalar(
                select(TranscriptionChunk.size_bytes).where(
                    TranscriptionChunk.recording_id == recording_id,
                    TranscriptionChunk.chunk_index == plan.chunk_index,
                )
            )
        try:
            metadata = self._storage.head_object(object_key)
        except StorageError as error:
            raise MediaError("media_storage_failed") from error
        if (
            metadata is None
            or metadata.size_bytes <= 0
            or (recorded_size is not None and metadata.size_bytes != recorded_size)
        ):
            chunk_output.unlink(missing_ok=True)
            self._media.render_chunk(normalized, chunk_output, plan)
            self._require_preparation_allowed(recording_id)
            try:
                self._storage.upload_file(object_key, chunk_output, "audio/flac")
                metadata = self._storage.head_object(object_key)
            except StorageError as error:
                raise MediaError("media_chunk_upload_failed") from error
            if metadata is None or metadata.size_bytes <= 0:
                raise MediaError("media_chunk_upload_failed")
        with self._sessions.begin() as database:
            chunk = database.scalar(
                select(TranscriptionChunk).where(
                    TranscriptionChunk.recording_id == recording_id,
                    TranscriptionChunk.chunk_index == plan.chunk_index,
                )
            )
            if chunk is None:
                raise MediaError("chunk_plan_missing")
            chunk.size_bytes = metadata.size_bytes

    def _mark_ready_for_transcription(self, recording_id: UUID, total_chunks: int) -> None:
        with self._sessions.begin() as database:
            recording = _recording(database, recording_id)
            count = database.scalar(
                select(func.count(TranscriptionChunk.id)).where(
                    TranscriptionChunk.recording_id == recording_id,
                    TranscriptionChunk.size_bytes.is_not(None),
                )
            )
            if total_chunks <= 0 or count != total_chunks:
                raise MediaError("chunk_plan_incomplete")
            recording.status = RecordingStatus.TRANSCRIBING
            recording.total_chunks = total_chunks

    def _require_preparation_allowed(self, recording_id: UUID) -> None:
        with self._sessions() as database:
            recording = database.get(Recording, recording_id)
            if recording is None or recording.status is RecordingStatus.DELETING:
                raise MediaError("recording_state_conflict")


def _recording(database: Session, recording_id: UUID) -> Recording:
    recording = database.get(Recording, recording_id)
    if recording is None:
        raise MediaError("recording_missing")
    if recording.status is RecordingStatus.DELETING:
        raise MediaError("recording_state_conflict")
    return recording


def _chunk_object_key(recording_id: UUID, chunk_index: int) -> str:
    return f"recordings/{recording_id}/chunks/{chunk_index:05d}.flac"


def _chunk_matches_plan(chunk: TranscriptionChunk, plan: ChunkPlan) -> bool:
    return (
        chunk.chunk_index == plan.chunk_index
        and abs(chunk.core_start_seconds - plan.core_start_seconds) < 1e-6
        and abs(chunk.core_end_seconds - plan.core_end_seconds) < 1e-6
        and abs(chunk.audio_start_seconds - plan.audio_start_seconds) < 1e-6
        and abs(chunk.audio_end_seconds - plan.audio_end_seconds) < 1e-6
        and chunk.object_key == _chunk_object_key(chunk.recording_id, chunk.chunk_index)
    )
