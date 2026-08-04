"""Reusable faster-whisper inference behind a small, testable boundary."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from transcriber.models import Language


class TranscriptionError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TranscriptWord:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    words: tuple[TranscriptWord, ...] = ()


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, language: Language) -> list[TranscriptSegment]: ...


class WhisperTranscriber:
    """Load one CPU int8 model and reuse it for every claimed chunk."""

    def __init__(
        self,
        *,
        model_name: str = "large-v3",
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: Path | None = None,
        model: Any | None = None,
    ) -> None:
        if model_name != "large-v3" or device != "cpu" or compute_type != "int8":
            raise ValueError("Whisper must use the approved large-v3 CPU int8 profile.")
        if model is None:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]

            model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
                download_root=str(download_root) if download_root is not None else None,
            )
        self._model = model

    def transcribe(self, audio_path: Path, language: Language) -> list[TranscriptSegment]:
        if language not in {Language.ENGLISH, Language.GERMAN, Language.TURKISH}:
            raise TranscriptionError("language_unsupported")
        try:
            raw_segments, _info = self._model.transcribe(
                str(audio_path),
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
                language=language.value,
            )
            segments = [
                segment
                for raw_segment in raw_segments
                if (segment := _parse_segment(raw_segment)) is not None
            ]
        except TranscriptionError:
            raise
        except Exception as error:
            raise TranscriptionError("transcription_failed") from error
        if not segments:
            raise TranscriptionError("transcript_empty")
        return segments


def clean_chunk_text(segments: list[TranscriptSegment]) -> str:
    return _normalize_whitespace(" ".join(segment.text for segment in segments))


def segments_to_json(segments: list[TranscriptSegment]) -> list[dict[str, object]]:
    return [
        {
            "start": round(segment.start_seconds, 3),
            "end": round(segment.end_seconds, 3),
            "text": segment.text,
            "words": [
                {
                    "start": round(word.start_seconds, 3),
                    "end": round(word.end_seconds, 3),
                    "text": word.text,
                }
                for word in segment.words
            ],
        }
        for segment in segments
    ]


def segments_from_json(payload: object) -> list[TranscriptSegment]:
    if not isinstance(payload, list):
        raise TranscriptionError("transcript_data_invalid")
    segments: list[TranscriptSegment] = []
    try:
        for raw_segment in payload:
            if not isinstance(raw_segment, dict):
                raise ValueError
            raw_words = raw_segment.get("words", [])
            if not isinstance(raw_words, list):
                raise ValueError
            words = tuple(
                TranscriptWord(
                    start_seconds=float(word["start"]),
                    end_seconds=float(word["end"]),
                    text=str(word["text"]),
                )
                for word in raw_words
                if isinstance(word, dict)
            )
            segment = TranscriptSegment(
                start_seconds=float(raw_segment["start"]),
                end_seconds=float(raw_segment["end"]),
                text=str(raw_segment["text"]),
                words=words,
            )
            if not _valid_segment(segment):
                raise ValueError
            segments.append(segment)
    except (KeyError, TypeError, ValueError) as error:
        raise TranscriptionError("transcript_data_invalid") from error
    return segments


def _parse_segment(raw_segment: object) -> TranscriptSegment | None:
    raw = cast(Any, raw_segment)
    try:
        start = float(raw.start)
        end = float(raw.end)
        text = _normalize_whitespace(str(raw.text))
    except (AttributeError, TypeError, ValueError):
        return None
    if not text or not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return None
    words: list[TranscriptWord] = []
    for raw_word in cast(list[object], getattr(raw, "words", None) or []):
        word = cast(Any, raw_word)
        try:
            word_start = max(start, float(word.start))
            word_end = min(end, float(word.end))
            word_text = _normalize_whitespace(str(word.word))
        except (AttributeError, TypeError, ValueError):
            continue
        if (
            word_text
            and math.isfinite(word_start)
            and math.isfinite(word_end)
            and word_end > word_start
        ):
            words.append(TranscriptWord(word_start, word_end, word_text))
    return TranscriptSegment(start, end, text, tuple(words))


def _valid_segment(segment: TranscriptSegment) -> bool:
    return (
        math.isfinite(segment.start_seconds)
        and math.isfinite(segment.end_seconds)
        and segment.start_seconds >= 0
        and segment.end_seconds > segment.start_seconds
        and bool(segment.text.strip())
        and all(
            word.start_seconds >= segment.start_seconds
            and word.end_seconds <= segment.end_seconds
            and word.end_seconds > word.start_seconds
            and bool(word.text.strip())
            for word in segment.words
        )
    )


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value, flags=re.UNICODE).strip()
