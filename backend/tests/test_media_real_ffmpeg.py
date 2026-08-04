from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from transcriber.media import MediaToolkit, plan_chunks

FFMPEG = os.environ.get("TEST_FFMPEG_PATH")
FFPROBE = os.environ.get("TEST_FFPROBE_PATH")
pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE,
    reason="Set TEST_FFMPEG_PATH and TEST_FFPROBE_PATH for real media checks.",
)


@pytest.mark.parametrize(
    ("filename", "codec"),
    [
        ("memo.m4a", "aac"),
        ("talk.mp3", "libmp3lame"),
        ("voice.wav", "pcm_s16le"),
        ("stream.aac", "aac"),
        ("lossless.flac", "flac"),
        ("audio.ogg", "libvorbis"),
        ("voice.opus", "libopus"),
        ("audio-only.mp4", "aac"),
    ],
)
def test_real_ffmpeg_decodes_supported_formats(tmp_path: Path, filename: str, codec: str) -> None:
    source = tmp_path / filename
    _generate_tone(source, codec)
    media = MediaToolkit(ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))

    probe = media.probe(source)

    assert 1.0 <= probe.duration_seconds <= 1.2
    assert probe.audio_codec


def test_real_ffmpeg_builds_normalized_playback_and_chunks(tmp_path: Path) -> None:
    source = tmp_path / "iphone-voice-memo.m4a"
    normalized = tmp_path / "normalized.flac"
    playback = tmp_path / "playback.m4a"
    chunk = tmp_path / "chunk.flac"
    _generate_tone(source, "aac")
    media = MediaToolkit(ffmpeg_path=str(FFMPEG), ffprobe_path=str(FFPROBE))

    probe = media.probe(source)
    media.normalize(source, normalized)
    media.create_playback(source, playback)
    silences = media.detect_silences(normalized, probe.duration_seconds)
    plan = plan_chunks(
        probe.duration_seconds,
        silences,
        core_seconds=1_200,
        boundary_search_seconds=30,
        overlap_seconds=5,
    )[0]
    media.render_chunk(normalized, chunk, plan)

    assert normalized.stat().st_size > 0
    assert playback.stat().st_size > 0
    assert chunk.stat().st_size > 0
    assert media.probe(playback).audio_codec == "aac"
    assert media.probe(chunk).audio_codec == "flac"


def _generate_tone(output: Path, codec: str) -> None:
    result = subprocess.run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.1",
            "-c:a",
            codec,
            "-y",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
