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
        media_path = cache / "video.mp4"
        mime_type = "video/mp4"
    else:
        vocals_path = cache / "vocals.wav"
        media_path = vocals_path if vocals_path.exists() else cache / "audio.mp3"
        mime_type = "audio/wav" if media_path.suffix == ".wav" else "audio/mpeg"

    if not media_path.exists():
        raise FileNotFoundError(f"Media not found: {media_path}. Run download first.")

    log.info(f"ep{episode}: uploading {media_path.name} to Gemini (mode={transcription_source})")
    file_uri = _upload_audio(media_path, api_key, mime_type)

    log.info(f"ep{episode}: transcribing + identifying + translating (all-in-one, source={transcription_source})")
    identified, char_genders = _call_gemini_allinone(
        file_uri, mime_type, characters, scenes, source_lang, target_lang, api_key, model,
        use_subtitle=(transcription_source == "video")
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
    file_uri = data["file"]["uri"]
    file_name = data["file"]["name"]

    if mime_type.startswith("video/"):
        _wait_for_processing(file_name, api_key)

    return file_uri


def _wait_for_processing(file_name: str, api_key: str):
    url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
    for i in range(30):
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            state = resp.json().get("state", "")
            if state == "ACTIVE":
                log.info("  video processing complete")
                return
            log.info(f"  waiting for video processing... ({state})")
        time.sleep(5)
    raise RuntimeError("Video processing timed out after 150s")


@retry(max_retries=3)
def _call_gemini_allinone(
    file_uri: str, mime_type: str, characters: dict,
    scenes: list[dict], source_lang: str, target_lang: str,
    api_key: str, model: str, use_subtitle: bool = False,
) -> tuple[list[Segment], dict[str, str]]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    if use_subtitle:
        prompt = _build_prompt_video(characters, scenes, source_lang, target_lang)
    else:
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
    idx = 0
    for r in results:
        start = float(r["start"])
        end = float(r["end"])
        duration = end - start

        if duration > 15.0 or duration <= 0:
            log.debug(f"  filtered segment with bad duration ({duration:.1f}s): {r.get('original', '')[:30]}")
            continue

        seg = Segment(
            index=idx,
            start=start,
            end=end,
            text=r.get("original", ""),
            character=r.get("character", "Unknown"),
            translation=r.get("translation", ""),
        )
        segments.append(seg)
        idx += 1
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


def _build_prompt_video(characters: dict, scenes: list[dict], source_lang: str, target_lang: str) -> str:
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

    return f"""You are a professional dubbing assistant. Watch this video carefully and perform ALL of the following tasks:

1. READ SUBTITLES: This video has hardcoded/burned-in subtitles in {source_name}. Read the subtitle text directly from the video frames — this is your PRIMARY source for the original dialogue text. Do NOT transcribe from audio.
2. TIMESTAMPS: Record the exact time each subtitle appears and disappears on screen (start = subtitle appears, end = subtitle disappears).
3. IDENTIFY: Determine which character is speaking each line by listening to the voice in the audio AND watching who is on screen.
4. GENDER: Determine the gender of each speaker from their voice AND visual appearance.
5. TRANSLATE: Translate each subtitle line from {source_name} to {target_name} for dubbing.

CRITICAL RULES:
- The subtitle text on screen is the GROUND TRUTH — use it exactly as shown, do not guess or transcribe from audio.
- Timestamps must match when the subtitle is VISIBLE on screen.
- Each subtitle appearance = one entry in the output.
- Do NOT merge multiple subtitle lines into one entry.
- Do NOT skip any subtitle that appears on screen.

KNOWN CHARACTERS:
{char_desc}

SCENE CONTEXT:
{scene_desc}

TRANSLATION RULES:
- Translate naturally into {target_name} — it must sound like natural spoken dialogue.
- Keep translations CONCISE — they must be speakable within the same duration as the original subtitle is shown.

SPEAKER IDENTIFICATION:
- Use voice characteristics (pitch, tone, age) AND visual cues to identify speakers.
- If a speaker doesn't match any known character, use "Speaker_1", "Speaker_2", etc.
- Different voices MUST get different speaker names.
- Male voices (deeper/lower pitch) → gender: "male"
- Female voices (higher pitch) → gender: "female"

Return a JSON array sorted by start time:
[{{"start": 29.8, "end": 30.9, "original": "subtitle text from video", "character": "Speaker_1", "gender": "female", "translation": "translated text"}}]

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
