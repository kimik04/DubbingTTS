from __future__ import annotations

import json
import logging
import platform
import sys
from pathlib import Path

from .utils import Segment, get_cache_dir, load_project_config, load_global_config, save_segments, load_segments

log = logging.getLogger(__name__)


def transcribe_episode(slug: str, episode: int, force: bool = False) -> list[Segment]:
    cache = get_cache_dir(slug, episode)
    output_path = cache / "whisper_segments.json"

    if not force and output_path.exists():
        log.info(f"ep{episode}: whisper cached, skipping")
        return load_segments(output_path)

    audio_path = cache / "audio.mp3"
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}. Run download first.")

    project = load_project_config(slug)
    global_config = load_global_config()
    source_lang = project["language"]["source"]
    model = global_config["models"]["whisper"]

    log.info(f"ep{episode}: transcribing with whisper (lang={source_lang})")
    segments = _run_whisper(str(audio_path), source_lang, model)
    log.info(f"ep{episode}: got {len(segments)} segments")
    save_segments(segments, output_path)
    return segments


def _run_whisper(audio_path: str, language: str, model: str) -> list[Segment]:
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        import mlx_whisper
        raw = mlx_whisper.transcribe(
            audio_path,
            language=language,
            path_or_hf_repo=model,
            word_timestamps=True,
        )
        return _parse_mlx_output(raw)
    else:
        from faster_whisper import WhisperModel
        fw_model = WhisperModel("small", device="cpu", compute_type="int8")
        segments_iter, _ = fw_model.transcribe(
            audio_path,
            language=language,
            word_timestamps=True,
        )
        return _parse_faster_output(segments_iter)


def _parse_mlx_output(raw: dict) -> list[Segment]:
    segments = []
    for i, seg in enumerate(raw.get("segments", [])):
        segments.append(Segment(
            index=i,
            start=seg["start"],
            end=seg["end"],
            text=seg["text"].strip(),
        ))
    return segments


def _parse_faster_output(segments_iter) -> list[Segment]:
    segments = []
    for i, seg in enumerate(segments_iter):
        segments.append(Segment(
            index=i,
            start=seg.start,
            end=seg.end,
            text=seg.text.strip(),
        ))
    return segments
