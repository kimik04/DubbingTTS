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

    if transcription_source == "video":
        video_path = cache / "video.mp4"
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")
        log.info(f"ep{episode}: uploading video.mp4 to Gemini")
        file_uri = _upload_file(video_path, api_key, "video/mp4")
        log.info(f"ep{episode}: single-pass video (subtitle + audio + timestamp)")
        identified, char_genders = _single_pass_video(
            file_uri, characters, scenes, source_lang, target_lang, api_key, model
        )
    else:
        vocals_path = cache / "vocals.wav"
        audio_path = vocals_path if vocals_path.exists() else cache / "audio.mp3"
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio not found. Run download + separate first.")
        audio_mime = "audio/wav" if audio_path.suffix == ".wav" else "audio/mpeg"
        log.info(f"ep{episode}: uploading {audio_path.name} to Gemini")
        file_uri = _upload_file(audio_path, api_key, audio_mime)
        log.info(f"ep{episode}: single-pass audio")
        identified, char_genders = _single_pass_audio(
            file_uri, audio_mime, characters, scenes, source_lang, target_lang, api_key, model
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


@retry()
def _upload_file(file_path: Path, api_key: str, mime_type: str) -> str:
    url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"

    file_size = file_path.stat().st_size
    headers = {
        "X-Goog-Upload-Command": "start, upload, finalize",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": mime_type,
    }

    with open(file_path, "rb") as f:
        resp = requests.post(url, headers=headers, data=f, timeout=180)

    resp.raise_for_status()
    data = resp.json()
    file_uri = data["file"]["uri"]
    file_name = data["file"]["name"]

    if mime_type.startswith("video/"):
        _wait_for_processing(file_name, api_key)

    return file_uri


def _wait_for_processing(file_name: str, api_key: str):
    log.info("  waiting 20s for video processing...")
    time.sleep(20)


@retry()
def _single_pass_video(file_uri, characters, scenes, source_lang, target_lang, api_key, model):
    """Single pass: video with hardcoded subtitles. Uses native Gemini timestamp format."""
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

    prompt = f"""Watch and listen to this video carefully.

Your task is to produce a transcription for voice dubbing. For each spoken line:
- Determine the TIMESTAMP (MM:SS) from when you HEAR the person speaking in the audio. Mark the moment speech begins.
- Transcribe what is said by listening to the audio.
- Identify the speaker by their voice characteristics and visual appearance (who is on screen, lip movement).
- Detect the emotion from the tone of voice.
- Translate into {target_name} naturally for dubbing. Keep translations concise — they should be speakable in roughly the same duration as the original speech.
- Each spoken line = one segment. Do NOT merge lines. Do NOT skip any spoken line.

KNOWN CHARACTERS (YOU MUST REUSE THESE EXACT NAMES if the same character appears):
{char_desc}
IMPORTANT: If a speaker matches any known character above, you MUST use that exact name. Only create a new name if the voice is clearly a different person not listed above.

SCENE CONTEXT:
{scene_desc}

Provide a brief summary at the beginning."""

    url = f"https://generativelanguage.googleapis.com/v1alpha/models/{model}:generateContent?key={api_key}"

    response_schema = {
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
                        "emotion": {
                            "type": "string",
                            "enum": ["happy", "sad", "angry", "neutral"]
                        }
                    },
                    "required": ["speaker", "timestamp", "content", "translation", "gender", "emotion"]
                }
            }
        },
        "required": ["summary", "segments"]
    }

    payload = {
        "contents": [{"parts": [
            {"fileData": {"mimeType": "video/mp4", "fileUri": file_uri}},
            {"text": prompt},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
            "mediaResolution": "media_resolution_high",
            "thinkingConfig": {"thinkingLevel": "high"},
        },
    }

    resp = requests.post(url, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    result = json.loads(text)

    return _parse_segments(result)


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

        emotion = seg.get("emotion", "neutral")
        translation = seg.get("translation", seg.get("content", ""))

        if translation == prev_translation and translation:
            continue
        prev_translation = translation

        if emotion != "neutral":
            translation = f"[{emotion}] {translation}"

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


@retry()
def _single_pass_audio(file_uri, mime_type, characters, scenes, source_lang, target_lang, api_key, model):
    """Single pass: audio only mode."""
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

    prompt = f"""Process this audio file and generate a detailed transcription.

Requirements:
1. Identify distinct speakers by voice characteristics.
2. Provide accurate timestamps for each segment (Format: MM:SS).
3. Detect the primary language of each segment.
4. Identify the primary emotion: happy, sad, angry, or neutral.
5. For each segment, also provide a translation into {target_name} that sounds natural for voice dubbing. Keep translations concise.

KNOWN CHARACTERS:
{char_desc}

SCENE CONTEXT:
{scene_desc}

Provide a brief summary at the beginning."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    response_schema = {
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
                        "emotion": {
                            "type": "string",
                            "enum": ["happy", "sad", "angry", "neutral"]
                        }
                    },
                    "required": ["speaker", "timestamp", "content", "translation", "gender", "emotion"]
                }
            }
        },
        "required": ["summary", "segments"]
    }

    payload = {
        "contents": [{"parts": [
            {"fileData": {"mimeType": mime_type, "fileUri": file_uri}},
            {"text": prompt},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
            "temperature": 0.1,
        },
    }

    resp = requests.post(url, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    result = json.loads(text)

    raw_segments = result.get("segments", [])
    char_genders = {}
    segments = []

    for i, seg in enumerate(raw_segments):
        start_ts = seg["timestamp"]
        if i + 1 < len(raw_segments):
            end_ts = raw_segments[i + 1]["timestamp"]
        else:
            end_ts = start_ts

        emotion = seg.get("emotion", "neutral")
        translation = seg.get("translation", seg.get("content", ""))
        if emotion != "neutral":
            translation = f"[{emotion}] {translation}"

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
