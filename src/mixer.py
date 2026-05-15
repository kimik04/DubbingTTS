from __future__ import annotations

import json
import logging
import subprocess
import shutil
import sys
import tempfile
from pathlib import Path

from .utils import get_cache_dir, get_output_dir, load_project_config, load_global_config, load_segments

log = logging.getLogger(__name__)


def mix_episode(slug: str, episode: int, force: bool = False) -> Path:
    cache = get_cache_dir(slug, episode)
    output_dir = get_output_dir(slug)
    output_path = output_dir / f"ep{episode}_dubbed.mp4"

    if not force and output_path.exists():
        log.info(f"ep{episode}: output cached, skipping mix")
        return output_path

    video_path = cache / "video.mp4"
    audio_path = cache / "audio.mp3"
    segments_path = cache / "identified_segments.json"

    for p in [video_path, audio_path, segments_path]:
        if not p.exists():
            raise FileNotFoundError(f"Required file not found: {p}")

    project = load_project_config(slug)
    global_config = load_global_config()
    sample_rate = global_config["audio"]["sample_rate"]
    bg_volume = project["audio"].get("bg_volume", 1.0)
    dub_volume = project["audio"].get("dub_volume", 0.7)

    segments = load_segments(segments_path)
    tts_dir = cache / "tts"

    bg_path = _get_background_audio(audio_path, cache, global_config)
    duration = _get_video_duration(video_path)

    log.info(f"ep{episode}: placing TTS segments on timeline")
    dub_raw = cache / "dub_raw.wav"
    _place_tts_segments(segments, tts_dir, duration, sample_rate, dub_raw)

    log.info(f"ep{episode}: boosting volume")
    dub_loud = cache / "dub_loud.wav"
    _boost_volume(dub_raw, dub_loud, dub_volume)

    log.info(f"ep{episode}: mixing with background")
    final_audio = cache / "final.mp3"
    _mix_with_background(dub_loud, bg_path, final_audio, bg_volume)

    log.info(f"ep{episode}: muxing video")
    _mux_video(video_path, final_audio, output_path)

    log.info(f"ep{episode}: done -> {output_path}")
    return output_path


def _get_background_audio(audio_path: Path, cache: Path, global_config: dict) -> Path:
    demucs_model = global_config.get("demucs", {}).get("model", "htdemucs")
    stem_name = audio_path.stem

    search_paths = [
        Path(tempfile.gettempdir()) / "demucs_out" / demucs_model / stem_name / "no_vocals.wav",
        cache / "demucs" / demucs_model / stem_name / "no_vocals.wav",
    ]

    for p in search_paths:
        if p.exists():
            log.info(f"  using cached demucs output: {p}")
            return p

    log.info("  running demucs separation")
    output_dir = cache / "demucs"
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems", "vocals",
        "-n", demucs_model,
        "-o", str(output_dir),
        str(audio_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    result = output_dir / demucs_model / stem_name / "no_vocals.wav"
    if not result.exists():
        raise FileNotFoundError(f"Demucs output not found: {result}")
    return result


def _get_video_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(video_path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def _place_tts_segments(segments, tts_dir: Path, duration: float, sample_rate: int, output: Path):
    silence = output.with_name("silence.wav")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", str(duration),
        str(silence),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    tts_files = []
    for seg in segments:
        if not seg.character:
            continue
        wav = tts_dir / seg.character / f"seg_{seg.index:04d}.wav"
        if wav.exists():
            tts_files.append((seg, wav))

    if not tts_files:
        log.warning("  no TTS files found, using silence")
        shutil.copy2(silence, output)
        return

    inputs = ["-i", str(silence)]
    filter_parts = []

    for i, (seg, wav) in enumerate(tts_files):
        inputs.extend(["-i", str(wav)])
        delay_ms = int(seg.start * 1000)
        slot_dur = seg.end - seg.start
        filter_parts.append(f"[{i+1}]atrim=duration={slot_dur:.3f},asetpts=PTS-STARTPTS,adelay={delay_ms}|{delay_ms}[d{i}]")

    mix_inputs = "[0]" + "".join(f"[d{i}]" for i in range(len(tts_files)))
    filter_parts.append(f"{mix_inputs}amix=inputs={len(tts_files)+1}:duration=first:dropout_transition=0:normalize=0")

    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, str(output)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    silence.unlink(missing_ok=True)


def _boost_volume(input_path: Path, output_path: Path, dub_volume: float):
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-af", f"volume={dub_volume},alimiter=limit=0.95",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _mix_with_background(dub_path: Path, bg_path: Path, output_path: Path, bg_volume: float):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(dub_path),
        "-i", str(bg_path),
        "-filter_complex",
        f"[1:a]volume={bg_volume}[bg];[0:a][bg]amix=inputs=2:duration=longest",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _mux_video(video_path: Path, audio_path: Path, output_path: Path):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac",
        "-map", "0:v:0", "-map", "1:a:0",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
