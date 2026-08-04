from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from transcriber.media import (
    ChunkPlan,
    MediaError,
    MediaToolkit,
    parse_silences,
    validate_local_size,
    validate_probe,
)


class FakeRunner:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self.results = results
        self.commands: list[list[str]] = []

    def run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        return self.results.pop(0)


def process_result(
    *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.mark.parametrize(
    ("format_name", "codec"),
    [
        ("mov,mp4,m4a,3gp,3g2,mj2", "aac"),
        ("mp3", "mp3"),
        ("wav", "pcm_s16le"),
        ("aac", "aac"),
        ("flac", "flac"),
        ("ogg", "vorbis"),
        ("ogg", "opus"),
    ],
)
def test_probe_accepts_supported_decodable_audio_profiles(format_name: str, codec: str) -> None:
    probe = validate_probe(
        {
            "format": {"format_name": format_name, "duration": "42.5"},
            "streams": [{"codec_type": "audio", "codec_name": codec}],
        }
    )

    assert probe.audio_codec == codec
    assert probe.duration_seconds == 42.5
    assert probe.container == format_name.split(",")[0]


def test_probe_requires_decodable_audio_and_a_bounded_duration() -> None:
    with pytest.raises(MediaError, match="media_no_audio"):
        validate_probe(
            {
                "format": {"format_name": "mp4", "duration": "10"},
                "streams": [{"codec_type": "video", "codec_name": "h264"}],
            }
        )

    with pytest.raises(MediaError, match="media_too_long"):
        validate_probe(
            {
                "format": {"format_name": "m4a", "duration": "14400.1"},
                "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            }
        )


def test_local_size_must_match_the_verified_bucket_size(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"1234")

    validate_local_size(source, 4)
    with pytest.raises(MediaError, match="media_size_mismatch"):
        validate_local_size(source, 5)


def test_media_toolkit_uses_fixed_argument_vectors(tmp_path: Path) -> None:
    payload = {
        "format": {"format_name": "mov,mp4,m4a", "duration": "60"},
        "streams": [{"codec_type": "audio", "codec_name": "aac"}],
    }
    runner = FakeRunner(
        [
            process_result(stdout=json.dumps(payload)),
            process_result(),
            process_result(),
            process_result(),
        ]
    )
    media = MediaToolkit(ffmpeg_path="safe-ffmpeg", ffprobe_path="safe-ffprobe", runner=runner)
    source = tmp_path / "memo;not-a-command.m4a"
    normalized = tmp_path / "normalized.flac"

    media.probe(source)
    media.normalize(source, normalized)
    media.create_playback(source, tmp_path / "playback.m4a")
    media.render_chunk(
        normalized,
        tmp_path / "chunk.flac",
        ChunkPlan(0, 0, 20, 0, 25),
    )

    assert runner.commands[0][0] == "safe-ffprobe"
    assert runner.commands[0][-1] == str(source)
    assert runner.commands[1][:5] == [
        "safe-ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
    ]
    assert ["-ac", "1"] == runner.commands[1][
        runner.commands[1].index("-ac") : runner.commands[1].index("-ac") + 2
    ]
    assert "128k" in runner.commands[2]
    assert "25.000" in runner.commands[3]


def test_silence_parser_reads_only_expected_timing_records() -> None:
    stderr = """
    input path /private/audio.m4a
    [silencedetect @ 1] silence_start: 10.25
    [silencedetect @ 1] silence_end: 12.75 | silence_duration: 2.5
    unexpected silence_start: nope
    [silencedetect @ 1] silence_start: 58
    """

    silences = parse_silences(stderr, 60)

    assert [(item.start_seconds, item.end_seconds) for item in silences] == [
        (10.25, 12.75),
        (58, 60),
    ]


def test_tool_failures_expose_only_stable_codes(tmp_path: Path) -> None:
    runner = FakeRunner([process_result(returncode=1, stderr="C:/private/source failed")])
    media = MediaToolkit(runner=runner)

    with pytest.raises(MediaError) as error:
        media.probe(tmp_path / "secret-name.m4a")

    assert str(error.value) == "media_unreadable"
    assert "secret-name" not in str(error.value)
