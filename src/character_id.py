from __future__ import annotations

import json
import logging
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

    vocals_path = cache / "vocals.wav"
    audio_path = vocals_path if vocals_path.exists() else cache / "audio.mp3"
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found. Run download + separate first.")

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

    log.info(f"ep{episode}: transcribing + identifying + translating (all-in-one)")
    identified, char_genders = _call_gemini_allinone(
        file_uri, mime_type, characters, scenes, source_lang, target_lang, api_key, model
    )

    new_chars = _handle_new_characters(slug, identified, characters, char_genders)
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
def _call_gemini_allinone(
    file_uri: str, mime_type: str, characters: dict,
    scenes: list[dict], source_lang: str, target_lang: str,
    api_key: str, model: str,
) -> tuple[list[Segment], dict[str, str]]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    prompt = _build_prompt(characters, scenes, source_lang, target_lang)

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

    resp = requests.post(url, json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    results = json.loads(text)

    char_genders = {}
    segments = []
    for i, r in enumerate(results):
        seg = Segment(
            index=i,
            start=float(r["start"]),
            end=float(r["end"]),
            text=r.get("original", ""),
            character=r.get("character", "Unknown"),
            translation=r.get("translation", ""),
        )
        segments.append(seg)
        gender = r.get("gender", "male")
        if seg.character not in char_genders:
            char_genders[seg.character] = gender

    return segments, char_genders


def _build_prompt(characters: dict, scenes: list[dict], source_lang: str, target_lang: str) -> str:
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

    return f"""You are a professional dubbing assistant. Listen to this audio carefully and perform ALL of the following tasks:

1. TRANSCRIBE: Detect every spoken line in the audio with precise timestamps (start and end in seconds).
2. IDENTIFY: Determine which character is speaking each line based on voice characteristics.
3. GENDER: Determine the gender of each speaker from their voice pitch and tone.
4. TRANSLATE: Translate each line from {source_name} to {target_name} for dubbing.

IMPORTANT RULES FOR TIMESTAMPS:
- Timestamps must be PRECISE to the actual moment each line is spoken in the audio.
- Start time = exact moment the person begins speaking that line.
- End time = exact moment the person finishes speaking that line.
- Do NOT overlap timestamps between different lines.
- Do NOT include silence/pauses in the timestamps.
- Listen carefully to the audio — accuracy of timestamps is critical for lip-sync dubbing.

KNOWN CHARACTERS:
{char_desc}

SCENE CONTEXT:
{scene_desc}

TRANSLATION RULES:
- Translate naturally into {target_name} — it must sound like natural spoken dialogue, not a literal translation.
- Keep translations CONCISE — they must fit within the same duration as the original line.
- Shorter is better. If the original is 1 second, the translation should be speakable in ~1 second.

SPEAKER IDENTIFICATION:
- Use voice characteristics (pitch, tone, age) to identify speakers.
- If a speaker doesn't match any known character, use "Speaker_1", "Speaker_2", etc.
- Different voices MUST get different speaker names.
- Male voices (deeper/lower pitch) → gender: "male"
- Female voices (higher pitch) → gender: "female"

Return a JSON array sorted by start time:
[{{"start": 29.8, "end": 30.9, "original": "original text", "character": "Speaker_1", "gender": "female", "translation": "translated text"}}]

Return ONLY the JSON array. No other text."""


def _handle_new_characters(slug: str, segments: list[Segment], characters: dict, char_genders: dict) -> list[str]:
    char_list = characters.get("characters", {})
    used_voices = {info.get("voice") for info in char_list.values()}
    new_chars = []

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
        char_list[seg.character] = {
            "voice": voice,
            "gender": gender,
            "description": f"Auto-detected {gender} character",
            "aliases": [],
            "first_seen": "ep1",
        }
        new_chars.append(seg.character)

    if new_chars:
        characters["characters"] = char_list
        save_characters(slug, characters)

    return new_chars
