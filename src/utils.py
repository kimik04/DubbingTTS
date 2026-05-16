import logging
import os
import time
import json
import functools
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass, asdict

import yaml

PROJECT_ROOT = Path(__file__).parent.parent

@dataclass
class Segment:
    index: int
    start: str
    end: str
    text: str
    character: Optional[str] = None
    translation: Optional[str] = None

    @property
    def start_sec(self) -> float:
        return parse_timestamp(self.start)

    @property
    def end_sec(self) -> float:
        return parse_timestamp(self.end)

    @property
    def duration(self) -> float:
        return self.end_sec - self.start_sec

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def load_global_config() -> dict:
    path = PROJECT_ROOT / "config.yaml"
    with open(path) as f:
        config = yaml.safe_load(f)
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        config["gemini_api_key"] = env_key
    return config


def load_project_config(slug: str) -> dict:
    path = PROJECT_ROOT / "projects" / slug / "project.yaml"
    with open(path) as f:
        project = yaml.safe_load(f)
    global_config = load_global_config()
    audio = {**global_config.get("audio", {}), **project.get("audio", {})}
    project["audio"] = audio
    project["_global"] = global_config
    return project


def load_characters(slug: str) -> dict:
    path = PROJECT_ROOT / "projects" / slug / "characters.yaml"
    if not path.exists():
        return {"characters": {}, "episodes": {}}
    with open(path) as f:
        return yaml.safe_load(f) or {"characters": {}, "episodes": {}}


def save_characters(slug: str, data: dict):
    path = PROJECT_ROOT / "projects" / slug / "characters.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def parse_links(slug: str) -> List[Tuple[int, str]]:
    path = PROJECT_ROOT / "projects" / slug / "links.txt"
    results = []
    ep_num = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ep_num += 1
            results.append((ep_num, line))
    return results


def parse_timestamp(ts: str) -> float:
    parts = ts.split(":")
    if len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return float(ts)


def get_cache_dir(slug: str, episode: int) -> Path:
    d = PROJECT_ROOT / "projects" / slug / "cache" / f"ep{episode}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_output_dir(slug: str) -> Path:
    d = PROJECT_ROOT / "projects" / slug / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def retry(max_retries=5, backoff_base=15.0, retryable_exceptions=(Exception,)):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    if attempt == max_retries:
                        raise
                    wait = backoff_base
                    logging.getLogger(__name__).warning(
                        f"{func.__name__} failed (attempt {attempt+1}/{max_retries}): {e}. Retrying in {wait:.0f}s"
                    )
                    time.sleep(wait)
        return wrapper
    return decorator


def retry_async(max_retries=3, backoff_base=2.0, retryable_exceptions=(Exception,)):
    import asyncio
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    if attempt == max_retries:
                        raise
                    wait = backoff_base ** attempt
                    logging.getLogger(__name__).warning(
                        f"{func.__name__} failed (attempt {attempt+1}/{max_retries}): {e}. Retrying in {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
        return wrapper
    return decorator


def save_segments(segments: list[Segment], path: Path):
    with open(path, "w") as f:
        json.dump([s.to_dict() for s in segments], f, ensure_ascii=False, indent=2)


def load_segments(path: Path) -> list[Segment]:
    with open(path) as f:
        data = json.load(f)
    return [Segment.from_dict(d) for d in data]
