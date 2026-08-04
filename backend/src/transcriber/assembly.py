"""Deterministic overlap removal and readable transcript paragraph assembly."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from transcriber.whisper_engine import TranscriptSegment


class AssemblyError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AssemblyChunk:
    chunk_index: int
    core_start_seconds: float
    core_end_seconds: float
    audio_start_seconds: float
    segments: tuple[TranscriptSegment, ...]


@dataclass(frozen=True)
class TimedToken:
    text: str
    start_seconds: float
    end_seconds: float


def assemble_transcript(
    chunks: Sequence[AssemblyChunk],
    *,
    overlap_window_seconds: float = 5,
    paragraph_pause_seconds: float = 2.5,
    paragraph_target_characters: int = 800,
) -> str:
    if (
        overlap_window_seconds < 0
        or paragraph_pause_seconds < 0
        or paragraph_target_characters <= 0
    ):
        raise ValueError("Transcript assembly settings must be positive.")
    ordered = sorted(chunks, key=lambda chunk: chunk.chunk_index)
    if not ordered or [chunk.chunk_index for chunk in ordered] != list(range(len(ordered))):
        raise AssemblyError("transcript_chunks_incomplete")

    assembled: list[TimedToken] = []
    previous_chunk: AssemblyChunk | None = None
    for chunk in ordered:
        current = _chunk_tokens(chunk)
        if not current:
            raise AssemblyError("transcript_chunk_empty")
        if previous_chunk is not None:
            boundary = chunk.core_start_seconds
            previous_boundary_tokens = [
                token
                for token in assembled
                if token.end_seconds >= boundary - overlap_window_seconds
            ]
            current_boundary_tokens = [
                token
                for token in current
                if token.start_seconds <= boundary + overlap_window_seconds
            ]
            overlap_count = _longest_overlap(previous_boundary_tokens, current_boundary_tokens)
            current = current[overlap_count:]
        assembled.extend(current)
        previous_chunk = chunk

    if not assembled:
        raise AssemblyError("transcript_empty")
    paragraphs = _paragraphs(
        assembled,
        pause_seconds=paragraph_pause_seconds,
        target_characters=paragraph_target_characters,
    )
    return "\n\n".join(paragraphs).strip() + "\n"


def _chunk_tokens(chunk: AssemblyChunk) -> list[TimedToken]:
    tokens: list[TimedToken] = []
    for segment in chunk.segments:
        segment_text = " ".join(segment.text.split())
        word_text = " ".join(word.text for word in segment.words)
        if segment.words and word_text == segment_text:
            tokens.extend(
                TimedToken(
                    text=word.text,
                    start_seconds=chunk.audio_start_seconds + word.start_seconds,
                    end_seconds=chunk.audio_start_seconds + word.end_seconds,
                )
                for word in segment.words
                if word.text.strip()
            )
            continue
        words = segment.text.split()
        if not words:
            continue
        duration = max(0.001, segment.end_seconds - segment.start_seconds)
        token_duration = duration / len(words)
        tokens.extend(
            TimedToken(
                text=word,
                start_seconds=chunk.audio_start_seconds
                + segment.start_seconds
                + index * token_duration,
                end_seconds=chunk.audio_start_seconds
                + segment.start_seconds
                + (index + 1) * token_duration,
            )
            for index, word in enumerate(words)
        )
    return tokens


def _longest_overlap(previous: list[TimedToken], current: list[TimedToken]) -> int:
    maximum = min(len(previous), len(current), 120)
    previous_normalized = [_normalize_token(token.text) for token in previous]
    current_normalized = [_normalize_token(token.text) for token in current]
    for length in range(maximum, 0, -1):
        previous_slice = previous_normalized[-length:]
        current_slice = current_normalized[:length]
        if all(previous_slice) and previous_slice == current_slice:
            return length
    return 0


def _normalize_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    start = 0
    end = len(normalized)
    while start < end and not normalized[start].isalnum():
        start += 1
    while end > start and not normalized[end - 1].isalnum():
        end -= 1
    return normalized[start:end]


def _paragraphs(
    tokens: list[TimedToken], *, pause_seconds: float, target_characters: int
) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    current_characters = 0
    previous_end: float | None = None
    sentence_end = re.compile(r"[.!?…][\"')\]]*$")
    for token in tokens:
        gap = token.start_seconds - previous_end if previous_end is not None else 0
        if current and gap >= pause_seconds:
            paragraphs.append(" ".join(current))
            current = []
            current_characters = 0
        current.append(token.text)
        current_characters += len(token.text) + (1 if len(current) > 1 else 0)
        previous_end = token.end_seconds
        if current_characters >= target_characters and sentence_end.search(token.text):
            paragraphs.append(" ".join(current))
            current = []
            current_characters = 0
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs
