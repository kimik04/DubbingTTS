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

    vocals_path = cache / "vocals.wav"
    if vocals_path.exists():
        audio_path = vocals_path
    else:
        audio_path = cache / "audio.mp3"
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found. Run download + separate first.")

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
            condition_on_previous_text=False,
            no_speech_threshold=0.5,
        )
        segments = _parse_mlx_output(raw)
    else:
        from faster_whisper import WhisperModel
        fw_model = WhisperModel("small", device="cpu", compute_type="int8")
        segments_iter, _ = fw_model.transcribe(
            audio_path,
            language=language,
            word_timestamps=True,
            condition_on_previous_text=False,
            no_speech_threshold=0.5,
        )
        segments = _parse_faster_output(segments_iter)
    return _filter_hallucinations(segments)


def _is_hallucination(text: str, duration: float) -> bool:
    if duration > 15.0:
        return True
    if len(text) < 2:
        return True
    if duration < 0.3 and len(text) > 3:
        return True
    clean = text.replace(" ", "")
    unique_chars = set(clean)
    if len(unique_chars) <= 3 and len(clean) > 4:
        return True
    if len(clean) > 6:
        for char in unique_chars:
            if clean.count(char) / len(clean) > 0.5:
                return True
    # Detect repeated substrings (e.g. "色色色色")
    if len(clean) >= 4:
        for size in range(1, 4):
            for start in range(len(clean) - size):
                sub = clean[start:start+size]
                if clean.count(sub) >= 3 and len(sub) * clean.count(sub) > len(clean) * 0.5:
                    return True
    return False


def _filter_hallucinations(segments: list[Segment]) -> list[Segment]:
    filtered = []
    idx = 0
    for seg in segments:
        duration = seg.end - seg.start
        if _is_hallucination(seg.text, duration):
            log.debug(f"  filtered hallucination: [{seg.start:.1f}-{seg.end:.1f}] {seg.text[:30]}")
            continue
        seg.index = idx
        filtered.append(seg)
        idx += 1
    if len(filtered) < len(segments):
        log.info(f"  filtered {len(segments) - len(filtered)} hallucinated segments")
    return filtered


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
