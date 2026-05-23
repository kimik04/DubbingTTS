from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import requests

from .utils import (
    Segment, get_cache_dir, load_project_config, load_global_config,
    load_characters, save_characters, save_segments, load_segments, retry,
)

log = logging.getLogger(__name__)

AVAILABLE_VOICES = {
    "male": ["Puck", "Charon", "Fenrir", "Orus", "Enceladus", "Iapetus", "Algenib", "Rasalgethi"],
    "female": ["Aoede", "Kore", "Zephyr", "Leda", "Callirrhoe", "Autonoe", "Despina", "Erinome"],
}


def _normalize_gender(g: str) -> str:
    g = g.lower().strip()
    if g in ("female", "perempuan", "wanita", "f"):
        return "female"
    return "male"


def identify_episode(slug: str, episode: int, force: bool = False) -> list[Segment]:
    cache = get_cache_dir(slug, episode)
    output_path = cache / "identified_segments.json"

    if not force and output_path.exists():
        log.info(f"ep{episode}: identification cached, skipping")
        return load_segments(output_path)

    project = load_project_config(slug)
    global_config = load_global_config()
    characters = load_characters(slug)
    api_key = global_config["gemini_api_key"]
    model = global_config["models"]["transcribe"]

    source_lang = project["language"]["source"]
    target_lang = project["language"]["target"]
    scenes = _get_scenes(project, episode)

    transcription_source = global_config.get("transcription", {}).get("source", "audio")

    if transcription_source in ("video", "subtitle"):
        video_path = cache / "video.mp4"
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")
        log.info(f"ep{episode}: processing video (mode={transcription_source})")
        if transcription_source == "subtitle":
            identified, char_genders = _single_pass_subtitle(
                video_path, characters, scenes, source_lang, target_lang, api_key, model
            )
        else:
            identified, char_genders = _single_pass_video(
                video_path, characters, scenes, source_lang, target_lang, api_key, model
            )
    else:
        vocals_path = cache / "vocals.wav"
        audio_path = vocals_path if vocals_path.exists() else cache / "audio.mp3"
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio not found. Run download + separate first.")
        log.info(f"ep{episode}: processing audio ({audio_path.name})")
        identified, char_genders = _single_pass_audio(
            audio_path, characters, scenes, source_lang, target_lang, api_key, model
        )

    new_chars = _handle_new_characters(slug, episode, identified, characters, char_genders)
    if new_chars:
        log.info(f"ep{episode}: added {len(new_chars)} new characters: {new_chars}")

    log.info(f"ep{episode}: got {len(identified)} segments")
    save_segments(identified, output_path)
    return identified


def _get_scenes(project: dict, episode: int) -> list[dict]:
    episodes = project.get("episodes", {})
    ep_key = f"ep{episode}"
    ep_data = episodes.get(ep_key, {})
    return ep_data.get("scenes", [])


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "content": {"type": "string"},
                    "translation": {"type": "string"},
                    "language": {"type": "string"},
                    "gender": {"type": "string"},
                    "intonation": {"type": "string"}
                },
                "required": ["speaker", "timestamp", "content", "translation", "gender", "intonation"]
            }
        }
    },
    "required": ["summary", "segments"]
}


@retry()
def _call_interactions(file_path: Path, mime_type: str, prompt: str, api_key: str, model: str) -> dict:
    url = f"https://generativelanguage.googleapis.com/v1beta/interactions"

    with open(file_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode()

    media_type = "video" if mime_type.startswith("video/") else "audio"

    payload = {
        "model": model,
        "input": [
            {"type": media_type, "data": b64_data, "mime_type": mime_type},
            {"type": "text", "text": prompt},
        ],
        "response_format": {"type": "text", "mime_type": "application/json", "schema": RESPONSE_SCHEMA},
        "store": False,
    }

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=300)
    if resp.status_code != 200:
        log.error(f"  API error {resp.status_code}: {resp.text[:500]}")
    resp.raise_for_status()
    data = resp.json()

    text_output = ""
    for out in data.get("outputs", []):
        if out.get("type") == "text":
            text_output = out.get("text", "")
            break

    if not text_output:
        raise RuntimeError(f"No text output in response. Keys: {list(data.keys())}")

    return json.loads(text_output)


@retry()
def _single_pass_video(file_path, characters, scenes, source_lang, target_lang, api_key, model):
    lang_names = {"zh": "Chinese", "en": "English", "ko": "Korean", "ja": "Japanese", "id": "Indonesian", "th": "Thai"}
    target_name = lang_names.get(target_lang, target_lang)

    char_desc = _build_char_desc(characters)
    scene_desc = _build_scene_desc(scenes)

    prompt = f"""Watch and listen to this video carefully.

Your task is to produce a transcription for voice dubbing. For each spoken line:
- Determine the TIMESTAMP (MM:SS) from when you HEAR the person speaking in the audio. Mark the moment speech begins.
- Transcribe what is said by listening to the audio.
- Identify the speaker by their voice characteristics and visual appearance (who is on screen, lip movement).
- For each line, provide a short English vocal direction cue in the "intonation" field describing HOW the line should be spoken (emotion, tone, speed, volume). Keep it under 10 words.
- Translate into {target_name} naturally for dubbing. Keep translations concise — they should be speakable in roughly the same duration as the original speech.
- Each spoken line = one segment. Do NOT merge lines. Do NOT skip any spoken line.

KNOWN CHARACTERS (YOU MUST REUSE THESE EXACT NAMES if the same character appears):
{char_desc}
IMPORTANT: If a speaker matches any known character above, you MUST use that exact name. Only create a new name if the voice is clearly a different person not listed above.

SCENE CONTEXT:
{scene_desc}

Provide a brief summary at the beginning."""

    result = _call_interactions(file_path, "video/mp4", prompt, api_key, model)
    return _parse_segments(result)


@retry()
def _single_pass_subtitle(file_path, characters, scenes, source_lang, target_lang, api_key, model):
    lang_names = {"zh": "Chinese", "en": "English", "ko": "Korean", "ja": "Japanese", "id": "Indonesian", "th": "Thai"}
    source_name = lang_names.get(source_lang, source_lang)
    target_name = lang_names.get(target_lang, target_lang)

    char_desc = _build_char_desc(characters)
    scene_desc = _build_scene_desc(scenes)

    prompt = f"""Watch this video carefully. It has hardcoded subtitles.

Your task:
- Read EVERY subtitle that appears on screen. The subtitle text is the ground truth.
- For each subtitle, record the TIMESTAMP (MM:SS) of when it APPEARS on screen.
- Identify the speaker by voice and visual appearance.
- For each line, provide a short English vocal direction cue in the "intonation" field describing HOW the line should be spoken (emotion, tone, speed, volume). Keep it under 10 words.
- If the subtitle is already in {target_name}, use it directly as the translation. Do NOT re-translate it.
- If the subtitle is in another language, translate it into {target_name} naturally for dubbing. Keep translations concise.
- Each subtitle appearance = one segment. Do NOT merge. Do NOT skip any subtitle.
- If there are multiple subtitle tracks on screen, prefer the {target_name} one. If none is in {target_name}, use the {source_name} one and translate it.

KNOWN CHARACTERS (YOU MUST REUSE THESE EXACT NAMES if the same character appears):
{char_desc}
IMPORTANT: If a speaker matches any known character above, you MUST use that exact name.

SCENE CONTEXT:
{scene_desc}

Provide a brief summary at the beginning."""

    result = _call_interactions(file_path, "video/mp4", prompt, api_key, model)
    return _parse_segments(result)


@retry()
def _single_pass_audio(file_path, characters, scenes, source_lang, target_lang, api_key, model):
    lang_names = {"zh": "Chinese", "en": "English", "ko": "Korean", "ja": "Japanese", "id": "Indonesian", "th": "Thai"}
    target_name = lang_names.get(target_lang, target_lang)

    char_desc = _build_char_desc(characters)
    scene_desc = _build_scene_desc(scenes)

    mime_type = "audio/wav" if file_path.suffix == ".wav" else "audio/mpeg"

    prompt = f"""Process this audio file and generate a detailed transcription.

Requirements:
1. Identify distinct speakers by voice characteristics.
2. Provide accurate timestamps for each segment (Format: MM:SS).
3. Detect the primary language of each segment.
4. For each line, provide a short English vocal direction cue in the "intonation" field describing HOW the line should be spoken (emotion, tone, speed, volume). Keep it under 10 words.
5. For each segment, also provide a translation into {target_name} that sounds natural for voice dubbing. Keep translations concise.

KNOWN CHARACTERS:
{char_desc}

SCENE CONTEXT:
{scene_desc}

Provide a brief summary at the beginning."""

    result = _call_interactions(file_path, mime_type, prompt, api_key, model)
    return _parse_segments(result)


def _build_char_desc(characters: dict) -> str:
    char_list = characters.get("characters", {})
    desc = ""
    for name, info in char_list.items():
        aliases = ", ".join(info.get("aliases", []))
        desc += f"- {name} ({info.get('gender', 'unknown')}): {info.get('description', '')}. Aliases: [{aliases}]\n"
    return desc


def _build_scene_desc(scenes: list[dict]) -> str:
    desc = ""
    for s in scenes:
        desc += f"- {s.get('time', '')}: {s.get('description', '')}\n"
    return desc


def _parse_segments(result: dict) -> tuple[list[Segment], dict]:
    from .utils import parse_timestamp

    raw_segments = result.get("segments", [])
    char_genders = {}
    segments = []
    prev_translation = ""

    for i, seg in enumerate(raw_segments):
        start_ts = seg["timestamp"]
        if i + 1 < len(raw_segments):
            end_ts = raw_segments[i + 1]["timestamp"]
        else:
            end_ts = start_ts

        intonation = seg.get("intonation", "")
        translation = seg.get("translation", seg.get("content", ""))

        if translation == prev_translation and translation:
            continue
        prev_translation = translation

        if intonation:
            translation = f"[{intonation}] {translation}"

        start_sec = parse_timestamp(start_ts)
        end_sec = parse_timestamp(end_ts)
        slot = end_sec - start_sec

        if slot > 8.0:
            end_sec = start_sec + 5.0
            m, s = divmod(int(end_sec), 60)
            end_ts = f"{m:02d}:{s:02d}"
        elif slot <= 0:
            end_sec = start_sec + 3.0
            m, s = divmod(int(end_sec), 60)
            end_ts = f"{m:02d}:{s:02d}"

        character = seg.get("speaker", "Unknown")
        gender = _normalize_gender(seg.get("gender", "male"))

        s = Segment(
            index=len(segments),
            start=start_ts,
            end=end_ts,
            text=seg.get("content", ""),
            character=character,
            translation=translation,
        )
        segments.append(s)

        if character not in char_genders:
            char_genders[character] = gender

    return segments, char_genders


def _handle_new_characters(slug: str, episode: int, segments: list[Segment], characters: dict, char_genders: dict) -> list[str]:
    char_list = characters.get("characters", {})
    used_voices = {info.get("voice") for info in char_list.values()}
    new_chars = []

    char_lines = {}
    for seg in segments:
        if seg.character:
            char_lines.setdefault(seg.character, []).append(seg.text[:30])

    for seg in segments:
        if not seg.character or seg.character in char_list:
            continue
        if seg.character in new_chars:
            continue

        gender = char_genders.get(seg.character, "male")
        pool = [v for v in AVAILABLE_VOICES[gender] if v not in used_voices]
        if not pool:
            other = "female" if gender == "male" else "male"
            pool = [v for v in AVAILABLE_VOICES[other] if v not in used_voices]
        if not pool:
            continue

        voice = pool[0]
        used_voices.add(voice)
        lines = char_lines.get(seg.character, [])
        desc = f"{gender.capitalize()} character, first lines: {'; '.join(lines[:3])}"
        char_list[seg.character] = {
            "voice": voice,
            "gender": gender,
            "description": desc,
            "aliases": [],
            "first_seen": f"ep{episode}",
        }
        new_chars.append(seg.character)

    if new_chars:
        characters["characters"] = char_list
        save_characters(slug, characters)

    return new_chars
