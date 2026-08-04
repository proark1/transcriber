"""Fixed-vector FFprobe and FFmpeg operations for private audio preparation."""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from transcriber.config import MAX_RECORDING_BYTES, MAX_RECORDING_SECONDS


class MediaError(RuntimeError):
    """A media failure represented by a stable, user-safe code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AudioProbe:
    container: str
    audio_codec: str
    duration_seconds: float


@dataclass(frozen=True)
class Silence:
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds

    @property
    def center_seconds(self) -> float:
        return (self.start_seconds + self.end_seconds) / 2


@dataclass(frozen=True)
class ChunkPlan:
    chunk_index: int
    core_start_seconds: float
    core_end_seconds: float
    audio_start_seconds: float
    audio_end_seconds: float


class ProcessRunner(Protocol):
    def run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]: ...


class SubprocessRunner:
    def run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(command),
                check=False,
                capture_output=True,
                text=True,
                timeout=None,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise MediaError("media_tool_unavailable") from error


class MediaToolkit:
    """Media operations with injectable process execution for deterministic tests."""

    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        runner: ProcessRunner | None = None,
    ) -> None:
        self._ffmpeg = ffmpeg_path
        self._ffprobe = ffprobe_path
        self._runner = runner or SubprocessRunner()

    def probe(self, source: Path) -> AudioProbe:
        result = self._runner.run(
            [
                self._ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name",
                "-show_entries",
                "stream=codec_type,codec_name,duration",
                "-of",
                "json",
                str(source),
            ]
        )
        if result.returncode != 0:
            raise MediaError("media_unreadable")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise MediaError("media_unreadable") from error
        if not isinstance(payload, Mapping):
            raise MediaError("media_unreadable")
        return validate_probe(payload)

    def normalize(self, source: Path, output: Path) -> None:
        self._run_ffmpeg(
            [
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "flac",
                "-compression_level",
                "5",
                "-y",
                str(output),
            ],
            "media_normalization_failed",
        )

    def create_playback(self, source: Path, output: Path) -> None:
        self._run_ffmpeg(
            [
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                "-y",
                str(output),
            ],
            "media_playback_failed",
        )

    def detect_silences(self, normalized: Path, duration_seconds: float) -> list[Silence]:
        result = self._runner.run(
            [
                self._ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(normalized),
                "-af",
                "silencedetect=noise=-35dB:d=0.8",
                "-f",
                "null",
                "-",
            ]
        )
        if result.returncode != 0:
            raise MediaError("media_silence_detection_failed")
        return parse_silences(result.stderr, duration_seconds)

    def render_chunk(self, normalized: Path, output: Path, plan: ChunkPlan) -> None:
        self._run_ffmpeg(
            [
                "-ss",
                f"{plan.audio_start_seconds:.3f}",
                "-i",
                str(normalized),
                "-t",
                f"{plan.audio_end_seconds - plan.audio_start_seconds:.3f}",
                "-map",
                "0:a:0",
                "-c:a",
                "flac",
                "-y",
                str(output),
            ],
            "media_chunk_failed",
        )

    def _run_ffmpeg(self, arguments: list[str], code: str) -> None:
        result = self._runner.run(
            [
                self._ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                *arguments,
            ]
        )
        if result.returncode != 0:
            raise MediaError(code)


def validate_local_size(source: Path, expected_bytes: int) -> None:
    try:
        actual_bytes = source.stat().st_size
    except OSError as error:
        raise MediaError("media_unreadable") from error
    if expected_bytes <= 0 or expected_bytes > MAX_RECORDING_BYTES:
        raise MediaError("media_too_large")
    if actual_bytes != expected_bytes:
        raise MediaError("media_size_mismatch")


def validate_probe(payload: Mapping[str, object]) -> AudioProbe:
    format_value = payload.get("format")
    streams_value = payload.get("streams")
    if not isinstance(format_value, Mapping) or not isinstance(streams_value, list):
        raise MediaError("media_unreadable")
    streams = [stream for stream in streams_value if isinstance(stream, Mapping)]
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if audio is None:
        raise MediaError("media_no_audio")

    duration = _positive_float(format_value.get("duration"))
    if duration is None:
        duration = _positive_float(audio.get("duration"))
    if duration is None:
        raise MediaError("media_invalid_duration")
    if duration > MAX_RECORDING_SECONDS:
        raise MediaError("media_too_long")

    codec = str(audio.get("codec_name", "")).strip().lower()
    container_names = str(format_value.get("format_name", "")).split(",")
    container = next((name.strip().lower() for name in container_names if name.strip()), "")
    if not codec or not container:
        raise MediaError("media_unreadable")
    return AudioProbe(container=container, audio_codec=codec, duration_seconds=duration)


def parse_silences(stderr: str, duration_seconds: float) -> list[Silence]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Silence parsing requires a positive duration.")
    start_pattern = re.compile(r"silence_start:\s*(-?[0-9]+(?:\.[0-9]+)?)")
    end_pattern = re.compile(r"silence_end:\s*(-?[0-9]+(?:\.[0-9]+)?)")
    pending_start: float | None = None
    silences: list[Silence] = []
    for line in stderr.splitlines():
        start_match = start_pattern.search(line)
        if start_match is not None:
            value = float(start_match.group(1))
            if math.isfinite(value):
                pending_start = max(0.0, min(value, duration_seconds))
            continue
        end_match = end_pattern.search(line)
        if end_match is None or pending_start is None:
            continue
        value = float(end_match.group(1))
        end = max(0.0, min(value, duration_seconds))
        if math.isfinite(value) and end > pending_start:
            silences.append(Silence(pending_start, end))
        pending_start = None
    if pending_start is not None and duration_seconds > pending_start:
        silences.append(Silence(pending_start, duration_seconds))
    return silences


def plan_chunks(
    duration_seconds: float,
    silences: Sequence[Silence],
    *,
    core_seconds: int,
    boundary_search_seconds: int,
    overlap_seconds: int,
) -> list[ChunkPlan]:
    if (
        not math.isfinite(duration_seconds)
        or duration_seconds <= 0
        or core_seconds <= 0
        or boundary_search_seconds < 0
        or overlap_seconds < 0
    ):
        raise ValueError("Chunk planning requires positive, finite settings.")
    boundaries = [0.0]
    nominal = float(core_seconds)
    while nominal < duration_seconds:
        candidates = [
            silence
            for silence in silences
            if abs(silence.center_seconds - nominal) <= boundary_search_seconds
            and silence.start_seconds >= boundaries[-1]
            and silence.end_seconds <= duration_seconds
        ]
        if candidates:
            selected = max(
                candidates,
                key=lambda silence: (
                    silence.duration_seconds,
                    -abs(silence.center_seconds - nominal),
                    -silence.center_seconds,
                ),
            )
            boundary = selected.center_seconds
        else:
            boundary = nominal
        if boundary <= boundaries[-1] or boundary >= duration_seconds:
            boundary = nominal
        boundaries.append(boundary)
        nominal += core_seconds
    boundaries.append(duration_seconds)

    return [
        ChunkPlan(
            chunk_index=index,
            core_start_seconds=start,
            core_end_seconds=end,
            audio_start_seconds=max(0.0, start - overlap_seconds),
            audio_end_seconds=min(duration_seconds, end + overlap_seconds),
        )
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:], strict=False))
    ]


def _positive_float(value: object) -> float | None:
    try:
        result = float(cast(str | float | int, value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result <= 0:
        return None
    return result
