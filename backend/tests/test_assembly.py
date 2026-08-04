from __future__ import annotations

import pytest

from transcriber.assembly import AssemblyChunk, AssemblyError, assemble_transcript
from transcriber.whisper_engine import TranscriptSegment


def chunk(
    index: int,
    text: str,
    *,
    core_start: float,
    core_end: float,
    audio_start: float,
    segment_start: float = 0,
    segment_end: float = 10,
) -> AssemblyChunk:
    return AssemblyChunk(
        chunk_index=index,
        core_start_seconds=core_start,
        core_end_seconds=core_end,
        audio_start_seconds=audio_start,
        segments=(TranscriptSegment(segment_start, segment_end, text),),
    )


def test_overlap_is_removed_without_changing_punctuation() -> None:
    result = assemble_transcript(
        [
            chunk(
                0,
                "Before the boundary, this is repeated.",
                core_start=0,
                core_end=1_200,
                audio_start=1_190,
            ),
            chunk(
                1,
                "This is repeated and then continues.",
                core_start=1_200,
                core_end=2_000,
                audio_start=1_195,
            ),
        ]
    )

    assert result == "Before the boundary, this is repeated. and then continues.\n"


def test_no_text_is_removed_when_boundary_words_do_not_match() -> None:
    result = assemble_transcript(
        [
            chunk(0, "First thought ends.", core_start=0, core_end=1_200, audio_start=1_190),
            chunk(
                1,
                "A different thought begins.",
                core_start=1_200,
                core_end=2_000,
                audio_start=1_195,
            ),
        ]
    )

    assert result == "First thought ends. A different thought begins.\n"


def test_unicode_german_compounds_and_turkish_casing_are_preserved() -> None:
    result = assemble_transcript(
        [
            chunk(
                0,
                "Donaudampfschifffahrtsgesellschaft. İstanbul'da görüşürüz.",
                core_start=0,
                core_end=100,
                audio_start=0,
            )
        ]
    )

    assert "Donaudampfschifffahrtsgesellschaft" in result
    assert "İstanbul'da görüşürüz." in result


def test_a_long_pause_starts_a_readable_paragraph() -> None:
    assembled = AssemblyChunk(
        chunk_index=0,
        core_start_seconds=0,
        core_end_seconds=20,
        audio_start_seconds=0,
        segments=(
            TranscriptSegment(0, 2, "First paragraph ends."),
            TranscriptSegment(5, 7, "Second paragraph begins."),
        ),
    )

    result = assemble_transcript([assembled])

    assert result == "First paragraph ends.\n\nSecond paragraph begins.\n"


def test_long_paragraph_breaks_at_the_next_sentence_boundary() -> None:
    repeated = "word " * 170
    result = assemble_transcript(
        [
            chunk(
                0,
                f"{repeated}sentence ends. A fresh sentence follows.",
                core_start=0,
                core_end=100,
                audio_start=0,
                segment_end=100,
            )
        ],
        paragraph_target_characters=800,
    )

    paragraphs = result.strip().split("\n\n")
    assert len(paragraphs) == 2
    assert paragraphs[0].endswith("sentence ends.")
    assert paragraphs[1] == "A fresh sentence follows."


def test_repeated_phrase_away_from_the_overlap_is_retained() -> None:
    result = assemble_transcript(
        [
            chunk(
                0,
                "Keep this repeated phrase but finish with unique words.",
                core_start=0,
                core_end=1_200,
                audio_start=1_190,
            ),
            chunk(
                1,
                "Keep this repeated phrase in the next section too.",
                core_start=1_200,
                core_end=2_000,
                audio_start=1_220,
            ),
        ]
    )

    assert result.count("Keep this repeated phrase") == 2


def test_missing_or_empty_chunks_fail_deterministically() -> None:
    with pytest.raises(AssemblyError, match="transcript_chunks_incomplete"):
        assemble_transcript([])

    with pytest.raises(AssemblyError, match="transcript_chunk_empty"):
        assemble_transcript(
            [
                AssemblyChunk(
                    chunk_index=0,
                    core_start_seconds=0,
                    core_end_seconds=1,
                    audio_start_seconds=0,
                    segments=(),
                )
            ]
        )
