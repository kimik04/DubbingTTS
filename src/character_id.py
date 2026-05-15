from __future__ import annotations

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
    "male": ["Puck", "Charon", "Fenrir", "Orus", "Leda"],
    "female": ["Aoede", "Kore", "Zephyr", "Elara", "Vesta"],
}


def identify_episode(slug: str, episode: int, force: bool = False) -> list[Segment]:
    cache = get_cache_dir(slug, episode)
    output_path = cache / "identified_segments.json"

    if not force and output_path.exists():
        log.info(f"ep{episode}: identification cached, skipping")
        return load_segments(output_path)

    whisper_path = cache / "whisper_segments.json"
    if not whisper_path.exists():
        raise FileNotFoundError(f"Whisper segments not found: {whisper_path}. Run transcribe first.")

    audio_path = cache / "audio.mp3"
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}. Run download first.")

    segments = load_segments(whisper_path)
    project = load_project_config(slug)
    global_config = load_global_config()
    characters = load_characters(slug)
    api_key = global_config["gemini_api_key"]
    model = global_config["models"]["transcribe"]

    source_lang = project["language"]["source"]
    target_lang = project["language"]["target"]
    scenes = _get_scenes(project, episode)

    log.info(f"ep{episode}: uploading audio to Gemini")
    file_uri = _upload_audio(audio_path, api_key)

    log.info(f"ep{episode}: identifying characters + translating")
    identified = _call_gemini_identify(
        file_uri, segments, characters, scenes, source_lang, target_lang, api_key, model
    )

    new_chars = _handle_new_characters(slug, identified, characters)
    if new_chars:
        log.info(f"ep{episode}: added {len(new_chars)} new characters: {new_chars}")

    save_segments(identified, output_path)
    return identified


def _get_scenes(project: dict, episode: int) -> list[dict]:
    episodes = project.get("episodes", {})
    ep_key = f"ep{episode}"
    ep_data = episodes.get(ep_key, {})
    return ep_data.get("scenes", [])


@retry(max_retries=3)
def _upload_audio(audio_path: Path, api_key: str) -> str:
    url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"

    file_size = audio_path.stat().st_size
    headers = {
        "X-Goog-Upload-Command": "start, upload, finalize",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": "audio/mpeg",
        "Content-Type": "audio/mpeg",
    }

    with open(audio_path, "rb") as f:
        resp = requests.post(url, headers=headers, data=f, timeout=120)

    resp.raise_for_status()
    data = resp.json()
    return data["file"]["uri"]


@retry(max_retries=3)
def _call_gemini_identify(
    file_uri: str, segments: list[Segment], characters: dict,
    scenes: list[dict], source_lang: str, target_lang: str,
    api_key: str, model: str,
) -> list[Segment]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    prompt = _build_prompt(segments, characters, scenes, source_lang, target_lang)

    payload = {
        "contents": [{
            "parts": [
                {"fileData": {"mimeType": "audio/mpeg", "fileUri": file_uri}},
                {"text": prompt},
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }

    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    results = json.loads(text)

    identified = []
    for seg in segments:
        match = next((r for r in results if r["index"] == seg.index), None)
        if match:
            seg.character = match.get("character", "Unknown")
            seg.translation = match.get("translation", seg.text)
        else:
            seg.character = "Unknown"
            seg.translation = seg.text
        identified.append(seg)

    return identified


def _build_prompt(segments: list[Segment], characters: dict, scenes: list[dict], source_lang: str, target_lang: str) -> str:
    lang_names = {"zh": "Chinese", "en": "English", "ko": "Korean", "ja": "Japanese", "id": "Indonesian", "th": "Thai"}
    source_name = lang_names.get(source_lang, source_lang)
    target_name = lang_names.get(target_lang, target_lang)

    char_list = characters.get("characters", {})
    char_desc = ""
    for name, info in char_list.items():
        aliases = ", ".join(info.get("aliases", []))
        char_desc += f"- {name} ({info.get('gender', 'unknown')}): {info.get('description', '')}. Aliases: [{aliases}]\n"

    scene_desc = ""
    for s in scenes:
        scene_desc += f"- {s.get('time', '')}: {s.get('description', '')}\n"

    seg_list = ""
    for s in segments:
        seg_list += f'{{"index": {s.index}, "start": {s.start:.2f}, "end": {s.end:.2f}, "text": "{s.text}"}}\n'

    return f"""You are a dubbing assistant. Listen to the audio and analyze the transcript segments below.

For each segment, identify which character is speaking and translate the text from {source_name} to {target_name}.

KNOWN CHARACTERS:
{char_desc}

SCENE CONTEXT:
{scene_desc}

TRANSCRIPT SEGMENTS:
{seg_list}

INSTRUCTIONS:
1. Listen to the audio to identify speakers by voice characteristics (gender, tone, age).
2. Use the scene context and character aliases to help identify speakers.
3. Translate each segment naturally into {target_name} (not literal, make it sound natural for dubbing).
4. If a speaker doesn't match any known character, use "Unknown_1", "Unknown_2", etc.
5. Keep translations concise - they need to fit the original timing for dubbing.

Return a JSON array with this format:
[{{"index": 0, "character": "CharacterName", "translation": "translated text"}}]

Return ONLY the JSON array, no other text."""


def _handle_new_characters(slug: str, segments: list[Segment], characters: dict) -> list[str]:
    char_list = characters.get("characters", {})
    used_voices = {info.get("voice") for info in char_list.values()}
    new_chars = []

    for seg in segments:
        if not seg.character or seg.character in char_list:
            continue
        if seg.character.startswith("Unknown"):
            gender = "male"
            pool = [v for v in AVAILABLE_VOICES[gender] if v not in used_voices]
            if not pool:
                pool = [v for v in AVAILABLE_VOICES["female"] if v not in used_voices]
            if not pool:
                continue
            voice = pool[0]
            used_voices.add(voice)
            char_list[seg.character] = {
                "voice": voice,
                "gender": gender,
                "description": "Auto-detected character",
                "aliases": [],
                "first_seen": f"ep{seg.index}",
            }
            new_chars.append(seg.character)

    if new_chars:
        characters["characters"] = char_list
        save_characters(slug, characters)

    return new_chars
