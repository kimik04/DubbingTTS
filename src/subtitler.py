from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from .utils import (
    PROJECT_ROOT, Segment, get_cache_dir, get_output_dir,
    load_project_config, load_segments,
)

log = logging.getLogger(__name__)


def _auto_config(width: int, height: int) -> dict:
    """Calculate subtitle config based on video dimensions."""
    ratio = width / height

    if ratio < 0.7:
        # Vertical (9:16) — subtitle di ~67% tinggi
        y_center = int(height * 0.67)
        font_size = max(16, int(width * 0.038))
        strip_height = int(height * 0.09)
    elif ratio < 1.0:
        # Square-ish (3:4, 2:3)
        y_center = int(height * 0.75)
        font_size = max(18, int(width * 0.035))
        strip_height = int(height * 0.08)
    else:
        # Horizontal (16:9, 4:3)
        y_center = int(height * 0.85)
        font_size = max(20, int(width * 0.022))
        strip_height = int(height * 0.08)

    char_w = max(8, int(font_size * 0.65))
    max_w = min(int(width * 0.93), width - 4)

    return {
        "font": "Arial",
        "font_size": font_size,
        "bold": True,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 2,
        "y_center": y_center,
        "strip_height": strip_height,
        "char_width": char_w,
        "min_width": max(100, int(width * 0.25)),
        "max_width": max_w,
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

    segments = [s for s in load_segments(segments_path) if _strip_intonation(s.translation)]
    if not segments:
        log.warning(f"ep{episode}: no translated segments, skipping")
        return dubbed

    width, height = _video_size(dubbed)
    project = load_project_config(slug)
    cfg = {**_auto_config(width, height), **(project.get("subtitle") or {})}

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
    strip_h = cfg["strip_height"]
    y_top = max(0, cfg["y_center"] - strip_h // 2)
    sigma = cfg["blur_sigma"]
    steps = cfg["blur_steps"]

    blur_w = min(int(video_w * 0.7), cfg["max_width"])
    blur_w -= blur_w % 2
    blur_x = (video_w - blur_w) // 2
    blur_x -= blur_x % 2

    enable_parts = []
    for seg in segments:
        start = seg.start_sec
        end = seg.end_sec if seg.end_sec > start else start + 2.0
        enable_parts.append(f"between(t\\,{start:.3f}\\,{end:.3f})")
    enable_expr = "+".join(enable_parts)

    rel_ass = ass_path.relative_to(PROJECT_ROOT)
    ass_arg = str(rel_ass).replace("\\", "/")

    filter_complex = (
        f"[0:v]split[base][forblur];"
        f"[forblur]crop={blur_w}:{strip_h}:{blur_x}:{y_top},gblur=sigma={sigma}:steps={steps}[blurred];"
        f"[base][blurred]overlay={blur_x}:{y_top}:enable='{enable_expr}'[withblur];"
        f"[withblur]ass={ass_arg}[outv]"
    )

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
