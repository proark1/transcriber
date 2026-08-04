from __future__ import annotations

import pytest

from transcriber.media import Silence, plan_chunks


def test_short_recording_is_one_bounded_chunk() -> None:
    chunks = plan_chunks(
        600,
        [],
        core_seconds=1_200,
        boundary_search_seconds=30,
        overlap_seconds=5,
    )

    assert len(chunks) == 1
    assert chunks[0].core_start_seconds == 0
    assert chunks[0].core_end_seconds == 600
    assert chunks[0].audio_start_seconds == 0
    assert chunks[0].audio_end_seconds == 600


def test_boundaries_choose_the_longest_nearby_silence_and_add_overlaps() -> None:
    chunks = plan_chunks(
        2_500,
        [
            Silence(1_198, 1_200),
            Silence(1_215, 1_221),
            Silence(2_397, 2_401),
        ],
        core_seconds=1_200,
        boundary_search_seconds=30,
        overlap_seconds=5,
    )

    assert len(chunks) == 3
    assert chunks[0].core_end_seconds == 1_218
    assert chunks[0].audio_end_seconds == 1_223
    assert chunks[1].audio_start_seconds == 1_213
    assert chunks[1].core_end_seconds == 2_399
    assert chunks[2].audio_start_seconds == 2_394
    assert chunks[2].audio_end_seconds == 2_500


def test_nominal_boundaries_are_used_without_nearby_silence() -> None:
    chunks = plan_chunks(
        2_401,
        [Silence(100, 110)],
        core_seconds=1_200,
        boundary_search_seconds=30,
        overlap_seconds=5,
    )

    assert [chunk.core_end_seconds for chunk in chunks] == [1_200, 2_400, 2_401]


def test_invalid_chunk_settings_are_rejected() -> None:
    with pytest.raises(ValueError):
        plan_chunks(
            0,
            [],
            core_seconds=1_200,
            boundary_search_seconds=30,
            overlap_seconds=5,
        )
