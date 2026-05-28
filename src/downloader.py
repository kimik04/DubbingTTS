from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .utils import get_cache_dir, load_project_config, load_global_config, parse_links, retry, PROJECT_ROOT

log = logging.getLogger(__name__)


def download_episode(slug: str, episode: int, url: str, force: bool = False) -> tuple[Path, Path]:
    cache = get_cache_dir(slug, episode)
    video_path = cache / "video.mp4"
    audio_path = cache / "audio.mp3"

    if not force and video_path.exists() and audio_path.exists():
        log.info(f"ep{episode}: cached, skipping download")
        return video_path, audio_path

    source = Path(url)
    if source.is_file():
        log.info(f"ep{episode}: copying local file {url}")
        shutil.copy2(source, video_path)
    else:
        log.info(f"ep{episode}: downloading {url}")
        cookies = PROJECT_ROOT / "projects" / slug / "cookies.txt"
        try:
            _download_url(url, video_path)
        except Exception as e:
            if cookies.exists():
                log.warning(f"ep{episode}: download failed, retrying with cookies.txt")
                _download_url(url, video_path, cookies_path=cookies)
            else:
                raise

    log.info(f"ep{episode}: extracting audio")
    _extract_audio(video_path, audio_path)

    return video_path, audio_path


def separate_audio(slug: str, episode: int, force: bool = False) -> tuple[Path, Path]:
    """Run demucs to separate vocals from background. Returns (vocals_path, no_vocals_path)."""
    cache = get_cache_dir(slug, episode)
    audio_path = cache / "audio.mp3"
    vocals_path = cache / "vocals.wav"
    no_vocals_path = cache / "no_vocals.wav"

    if not force and vocals_path.exists() and no_vocals_path.exists():
        log.info(f"ep{episode}: demucs cached, skipping separation")
        return vocals_path, no_vocals_path

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}. Run download first.")

    global_config = load_global_config()
    demucs_model = global_config.get("demucs", {}).get("model", "htdemucs")

    log.info(f"ep{episode}: separating vocals with demucs")
    demucs_out = cache / "demucs"
    demucs_out.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "demucs",
        "--two-stems", "vocals",
        "-n", demucs_model,
        "-o", str(demucs_out),
        str(audio_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    stem_name = audio_path.stem
    src_vocals = demucs_out / demucs_model / stem_name / "vocals.wav"
    src_no_vocals = demucs_out / demucs_model / stem_name / "no_vocals.wav"

    shutil.copy2(src_vocals, vocals_path)
    shutil.copy2(src_no_vocals, no_vocals_path)

    log.info(f"ep{episode}: separation done")
    return vocals_path, no_vocals_path


@retry(max_retries=3)
def _download_url(url: str, output: Path, cookies_path: Path | None = None):
    cmd = [
        "yt-dlp", "--no-playlist",
        "-o", str(output),
        "--merge-output-format", "mp4",
    ]
    if cookies_path:
        cmd.extend(["--cookies", str(cookies_path)])
    cmd.append(url)
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _has_audio_stream(video: Path) -> bool:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        str(video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return bool(result.stdout.strip())


def _extract_audio(video: Path, audio: Path):
    if not _has_audio_stream(video):
        log.warning("  video has no audio stream, creating silent audio")
        duration_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)]
        dur = float(subprocess.run(duration_cmd, check=True, capture_output=True, text=True).stdout.strip())
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
            "-t", str(dur),
            "-acodec", "libmp3lame",
            str(audio),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return

    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-acodec", "libmp3lame", "-q:a", "2",
        str(audio),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def download_all(slug: str, episode_filter: int | None = None) -> list[tuple[int, Path, Path]]:
    links = parse_links(slug)
    results = []
    for ep_num, url in links:
        if episode_filter and ep_num != episode_filter:
            continue
        video, audio = download_episode(slug, ep_num, url)
        results.append((ep_num, video, audio))
    return results
