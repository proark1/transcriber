from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from transcriber.models import Language
from transcriber.whisper_engine import (
    TranscriptionError,
    WhisperTranscriber,
    clean_chunk_text,
    segments_from_json,
    segments_to_json,
)


@dataclass
class FakeModel:
    segments: list[object]

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def transcribe(self, audio_path: str, **kwargs: Any) -> tuple[list[object], object]:
        self.calls.append((audio_path, kwargs))
        return self.segments, SimpleNamespace(language=kwargs["language"])


def raw_segment(
    start: float,
    end: float,
    text: str,
    words: list[tuple[float, float, str]] | None = None,
) -> object:
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        words=[SimpleNamespace(start=a, end=b, word=value) for a, b, value in words or []],
    )


@pytest.mark.parametrize("language", list(Language))
def test_whisper_forces_the_selected_language_and_accuracy_profile(
    tmp_path: Path, language: Language
) -> None:
    model = FakeModel(
        [raw_segment(0, 2, "  Hello   world. ", [(0, 1, " Hello"), (1, 2, " world.")])]
    )
    transcriber = WhisperTranscriber(model=model)
    audio = tmp_path / "chunk.flac"

    segments = transcriber.transcribe(audio, language)

    assert segments[0].text == "Hello world."
    assert [word.text for word in segments[0].words] == ["Hello", "world."]
    assert model.calls == [
        (
            str(audio),
            {
                "beam_size": 5,
                "vad_filter": True,
                "word_timestamps": True,
                "language": language.value,
            },
        )
    ]


def test_invalid_segments_are_filtered_and_an_empty_result_fails(tmp_path: Path) -> None:
    model = FakeModel(
        [
            raw_segment(2, 1, "backwards"),
            raw_segment(0, 1, "  "),
        ]
    )
    transcriber = WhisperTranscriber(model=model)

    with pytest.raises(TranscriptionError, match="transcript_empty"):
        transcriber.transcribe(tmp_path / "empty.flac", Language.ENGLISH)


def test_model_failures_do_not_expose_private_details(tmp_path: Path) -> None:
    class BrokenModel:
        def transcribe(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("C:/private/customer-name.m4a")

    transcriber = WhisperTranscriber(model=BrokenModel())

    with pytest.raises(TranscriptionError) as error:
        transcriber.transcribe(tmp_path / "private.flac", Language.GERMAN)

    assert str(error.value) == "transcription_failed"
    assert "customer" not in str(error.value)


def test_segments_round_trip_as_private_timestamped_json() -> None:
    model = FakeModel([raw_segment(0, 2, "Merhaba dünya.", [(0, 1, "Merhaba"), (1, 2, "dünya.")])])
    transcriber = WhisperTranscriber(model=model)
    segments = transcriber.transcribe(Path("chunk.flac"), Language.TURKISH)

    restored = segments_from_json(segments_to_json(segments))

    assert restored == segments
    assert clean_chunk_text(restored) == "Merhaba dünya."


def test_only_large_v3_cpu_int8_can_be_selected() -> None:
    with pytest.raises(ValueError):
        WhisperTranscriber(model_name="small", model=FakeModel([]))
