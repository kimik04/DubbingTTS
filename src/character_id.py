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

    # Use vocals.wav for better speaker identification
    vocals_path = cache / "vocals.wav"
    audio_path = vocals_path if vocals_path.exists() else cache / "audio.mp3"
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found. Run download + separate first.")

    segments = load_segments(whisper_path)
    project = load_project_config(slug)
    global_config = load_global_config()
    characters = load_characters(slug)
    api_key = global_config["gemini_api_key"]
    model = global_config["models"]["transcribe"]

    source_lang = project["language"]["source"]
    target_lang = project["language"]["target"]
    scenes = _get_scenes(project, episode)

    mime_type = "audio/wav" if audio_path.suffix == ".wav" else "audio/mpeg"

    log.info(f"ep{episode}: uploading audio to Gemini ({audio_path.name})")
    file_uri = _upload_audio(audio_path, api_key, mime_type)

    log.info(f"ep{episode}: identifying characters + translating")
    identified, char_genders = _call_gemini_identify(
        file_uri, mime_type, segments, characters, scenes, source_lang, target_lang, api_key, model
    )

    new_chars = _handle_new_characters(slug, identified, characters, char_genders)
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
def _upload_audio(audio_path: Path, api_key: str, mime_type: str) -> str:
    url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"

    file_size = audio_path.stat().st_size
    headers = {
        "X-Goog-Upload-Command": "start, upload, finalize",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": mime_type,
    }

    with open(audio_path, "rb") as f:
        resp = requests.post(url, headers=headers, data=f, timeout=120)

    resp.raise_for_status()
    data = resp.json()
    return data["file"]["uri"]


@retry(max_retries=3)
def _call_gemini_identify(
    file_uri: str, mime_type: str, segments: list[Segment], characters: dict,
    scenes: list[dict], source_lang: str, target_lang: str,
    api_key: str, model: str,
) -> tuple[list[Segment], dict[str, str]]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    prompt = _build_prompt(segments, characters, scenes, source_lang, target_lang)

    payload = {
        "contents": [{
            "parts": [
                {"fileData": {"mimeType": mime_type, "fileUri": file_uri}},
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

    char_genders = {}
    identified = []
    for seg in segments:
        match = next((r for r in results if r["index"] == seg.index), None)
        if match:
            seg.character = match.get("character", "Unknown")
            seg.translation = match.get("translation", seg.text)
            gender = match.get("gender", "male")
            if seg.character not in char_genders:
                char_genders[seg.character] = gender
        else:
            seg.character = "Unknown"
            seg.translation = seg.text
        identified.append(seg)

    return identified, char_genders


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

For each segment, identify which character is speaking, determine their gender from their voice, and translate the text from {source_name} to {target_name}.

KNOWN CHARACTERS:
{char_desc}

SCENE CONTEXT:
{scene_desc}

TRANSCRIPT SEGMENTS:
{seg_list}

INSTRUCTIONS:
1. Listen to the audio carefully to identify speakers by voice characteristics (gender, tone, age, pitch).
2. IMPORTANT: Determine the gender of each speaker from their VOICE in the audio. Male voices are deeper/lower pitch. Female voices are higher pitch.
3. Use the scene context and character aliases to help identify speakers.
4. Translate each segment naturally into {target_name} (not literal, make it sound natural for dubbing).
5. If a speaker doesn't match any known character, use "Unknown_1", "Unknown_2", etc. Give different names to different speakers.
6. Keep translations concise - they need to fit the original timing for dubbing.

Return a JSON array with this format:
[{{"index": 0, "character": "CharacterName", "gender": "male", "translation": "translated text"}}]

The "gender" field MUST be either "male" or "female" based on the actual voice you hear in the audio.

Return ONLY the JSON array, no other text."""


def _handle_new_characters(slug: str, segments: list[Segment], characters: dict, char_genders: dict) -> list[str]:
    char_list = characters.get("characters", {})
    used_voices = {info.get("voice") for info in char_list.values()}
    new_chars = []

    for seg in segments:
        if not seg.character or seg.character in char_list:
            continue
        if seg.character in [c for c in new_chars]:
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
        char_list[seg.character] = {
            "voice": voice,
            "gender": gender,
            "description": f"Auto-detected {gender} character",
            "aliases": [],
            "first_seen": f"ep1",
        }
        new_chars.append(seg.character)

    if new_chars:
        characters["characters"] = char_list
        save_characters(slug, characters)

    return new_chars
