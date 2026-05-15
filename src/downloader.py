from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .utils import get_cache_dir, load_project_config, parse_links, retry

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
        _download_url(url, video_path)

    log.info(f"ep{episode}: extracting audio")
    _extract_audio(video_path, audio_path)

    return video_path, audio_path


@retry(max_retries=3)
def _download_url(url: str, output: Path):
    cmd = [
        "yt-dlp", "--no-playlist",
        "-o", str(output),
        "--merge-output-format", "mp4",
        url,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _extract_audio(video: Path, audio: Path):
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
