from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from .utils import (
    PROJECT_ROOT, Segment, get_cache_dir, get_output_dir,
    load_global_config, load_segments,
)

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "font": "Arial",
    "font_size": 22,
    "bold": True,
    "primary_color": "&H0000FFFF",
    "outline_color": "&H00000000",
    "outline": 2,
    "y_center": 690,
    "strip_height": 100,
    "char_width": 14,
    "min_width": 140,
    "max_width": 540,
    "padding": 20,
    "blur_sigma": 50,
    "blur_steps": 4,
}


def subtitle_episode(slug: str, episode: int, force: bool = False) -> Path:
    cache = get_cache_dir(slug, episode)
    output_dir = get_output_dir(slug)
    dubbed = output_dir / f"ep{episode}_dubbed.mp4"
    segments_path = cache / "identified_segments.json"
    marker = cache / ".subtitled"

    if not dubbed.exists():
        raise FileNotFoundError(f"Dubbed video not found: {dubbed}. Run mix first.")
    if not segments_path.exists():
        raise FileNotFoundError(f"Segments not found: {segments_path}.")

    if not force and marker.exists() and marker.stat().st_mtime >= dubbed.stat().st_mtime:
        log.info(f"ep{episode}: subtitle already applied, skipping")
        return dubbed

    cfg = {**DEFAULT_CONFIG, **(load_global_config().get("subtitle") or {})}
    segments = [s for s in load_segments(segments_path) if _strip_intonation(s.translation)]
    if not segments:
        log.warning(f"ep{episode}: no translated segments, skipping")
        return dubbed

    width, height = _video_size(dubbed)
    ass_path = cache / "subs.ass"
    _write_ass(ass_path, segments, width, height, cfg)

    log.info(f"ep{episode}: rendering {len(segments)} subtitles with dynamic blur")
    temp = cache / f"ep{episode}_subtitled.mp4"
    _render(dubbed, ass_path, segments, width, temp, cfg)

    shutil.move(str(temp), str(dubbed))
    marker.write_text("ok", encoding="utf-8")
    log.info(f"ep{episode}: subtitled -> {dubbed}")
    return dubbed


def _strip_intonation(text):
    return re.sub(r'^\s*\[[^\]]*\]\s*', '', text or '').strip()


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _fmt_ts(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _video_size(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        str(path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    w, h = result.stdout.strip().split("x")
    return int(w), int(h)


def _write_ass(path: Path, segments: list[Segment], width: int, height: int, cfg: dict):
    bold = -1 if cfg["bold"] else 0
    cx = width // 2
    cy = cfg["y_center"]

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{cfg['font']},{cfg['font_size']},{cfg['primary_color']},&H000000FF,"
        f"{cfg['outline_color']},&H00000000,{bold},0,0,0,100,100,0,0,1,{cfg['outline']},0,5,10,10,10,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for seg in segments:
        text = _escape_ass(_strip_intonation(seg.translation))
        start = seg.start_sec
        end = seg.end_sec if seg.end_sec > start else start + 2.0
        lines.append(
            f"Dialogue: 0,{_fmt_ts(start)},{_fmt_ts(end)},Default,,0,0,0,,"
            f"{{\\pos({cx},{cy})}}{text}"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render(input_path: Path, ass_path: Path, segments: list[Segment], video_w: int, output: Path, cfg: dict):
    n = len(segments)
    splits = "".join(f"[s{i}]" for i in range(n + 1))
    parts = [f"[0:v]split={n+1}{splits}"]

    strip_h = cfg["strip_height"]
    y_top = max(0, cfg["y_center"] - strip_h // 2)
    sigma = cfg["blur_sigma"]
    steps = cfg["blur_steps"]
    char_w = cfg["char_width"]
    pad = cfg["padding"]
    min_w = cfg["min_width"]
    max_w = min(cfg["max_width"], video_w - 4)

    cur = "s0"
    for i, seg in enumerate(segments):
        text = _strip_intonation(seg.translation)
        text_len = max(1, len(text))
        w = min(max_w, max(min_w, text_len * char_w + pad * 2))
        w -= w % 2
        x = (video_w - w) // 2
        x -= x % 2
        start = seg.start_sec
        end = seg.end_sec if seg.end_sec > start else start + 2.0
        parts.append(
            f"[s{i+1}]crop={w}:{strip_h}:{x}:{y_top},"
            f"gblur=sigma={sigma}:steps={steps}[b{i}]"
        )
        parts.append(
            f"[{cur}][b{i}]overlay={x}:{y_top}:enable='between(t,{start:.3f},{end:.3f})'[v{i}]"
        )
        cur = f"v{i}"

    rel_ass = ass_path.relative_to(PROJECT_ROOT)
    ass_arg = str(rel_ass).replace("\\", "/")
    parts.append(f"[{cur}]ass={ass_arg}[outv]")
    filter_complex = ";".join(parts)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=PROJECT_ROOT)
