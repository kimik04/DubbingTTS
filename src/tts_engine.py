from __future__ import annotations

import asyncio
import base64
import json
import logging
import subprocess
import wave
from pathlib import Path

import websockets

from .utils import (
    Segment, get_cache_dir, load_project_config, load_global_config,
    load_characters, load_segments, retry_async,
)

log = logging.getLogger(__name__)


def generate_tts_episode_sync(slug: str, episode: int, character_filter: str | None = None, force: bool = False) -> dict[str, list[Path]]:
    return asyncio.run(generate_tts_episode(slug, episode, character_filter, force))


async def generate_tts_episode(slug: str, episode: int, character_filter: str | None = None, force: bool = False) -> dict[str, list[Path]]:
    cache = get_cache_dir(slug, episode)
    segments_path = cache / "identified_segments.json"

    if not segments_path.exists():
        raise FileNotFoundError(f"Identified segments not found: {segments_path}. Run identify first.")

    segments = load_segments(segments_path)
    project = load_project_config(slug)
    global_config = load_global_config()
    characters = load_characters(slug)

    api_key = global_config["gemini_api_key"]
    model = global_config["models"]["tts"]
    sample_rate = global_config["audio"]["sample_rate"]
    max_speed = global_config["audio"]["max_speed"]
    target_lang = project["language"]["target"]

    char_segments: dict[str, list[Segment]] = {}
    for seg in segments:
        if not seg.character or not seg.translation:
            continue
        char_segments.setdefault(seg.character, []).append(seg)

    results = {}
    char_list = characters.get("characters", {})

    for character, segs in char_segments.items():
        if character_filter and character != character_filter:
            continue

        voice = char_list.get(character, {}).get("voice", "Puck")
        tts_dir = cache / "tts" / character
        tts_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"ep{episode}: TTS for {character} ({len(segs)} segments, voice={voice})")
        paths = await _generate_character_tts(
            character, voice, segs, target_lang, tts_dir,
            sample_rate, max_speed, api_key, model,force
        )
        results[character] = paths

    return results


async def _generate_character_tts(
    character: str, voice: str, segments: list[Segment],
    target_lang: str, tts_dir: Path, sample_rate: int,
    max_speed: float, api_key: str, model: str, force: bool,
) -> list[Path]:
    lang_names = {"id": "Indonesian", "en": "English", "zh": "Chinese", "ms": "Malay", "th": "Thai"}
    target_name = lang_names.get(target_lang, target_lang)

    paths = []
    pending = []

    for seg in segments:
        wav_path = tts_dir / f"seg_{seg.index:04d}.wav"
        paths.append(wav_path)
        if not force and wav_path.exists():
            continue
        pending.append((seg, wav_path))

    if not pending:
        log.info(f"  {character}: all segments cached")
        return paths

    log.info(f"  {character}: generating {len(pending)} segments")

    ws = await _connect_ws(api_key, model)
    try:
        await _send_setup(ws, voice, target_name, model)

        for seg, wav_path in pending:
            slot_duration = seg.end - seg.start
            try:
                pcm = await _generate_segment(ws, seg.translation, slot_duration)
                _pcm_to_wav(pcm, wav_path, sample_rate)
            except Exception as e:
                log.warning(f"  {character} seg_{seg.index}: failed ({e}), reconnecting")
                try:
                    await ws.close()
                except:
                    pass
                ws = await _connect_ws(api_key, model)
                await _send_setup(ws, voice, target_name, model)
                try:
                    pcm = await _generate_segment(ws, seg.translation, slot_duration)
                    _pcm_to_wav(pcm, wav_path, sample_rate)
                except Exception as e2:
                    log.error(f"  {character} seg_{seg.index}: failed after retry ({e2})")
    finally:
        await ws.close()

    return paths


async def _connect_ws(api_key: str, model: str):
    url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"
    ws = await websockets.connect(url, max_size=None)
    return ws


async def _send_setup(ws, voice: str, target_lang: str, model: str):
    setup_msg = {
        "setup": {
            "model": f"models/{model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice}
                    }
                }
            },
            "systemInstruction": {
                "parts": [{"text": f"You are a professional voice dubbing actor. Dub the following lines into {target_lang}. Speak naturally and expressively with appropriate emotion."}]
            }
        }
    }
    await ws.send(json.dumps(setup_msg))

    resp = await asyncio.wait_for(ws.recv(), timeout=10)
    data = json.loads(resp)
    if "setupComplete" not in data:
        raise RuntimeError(f"Setup failed: {data}")


async def _generate_segment(ws, text: str, duration: float = 0) -> bytes:
    msg = {
        "clientContent": {
            "turns": [{"role": "user", "parts": [{"text": text}]}],
            "turnComplete": True,
        }
    }
    await ws.send(json.dumps(msg))

    pcm_chunks = []
    while True:
        resp = await asyncio.wait_for(ws.recv(), timeout=30)
        data = json.loads(resp)

        server_content = data.get("serverContent")
        if not server_content:
            continue

        if server_content.get("turnComplete"):
            break

        parts = server_content.get("modelTurn", {}).get("parts", [])
        for part in parts:
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                pcm_chunks.append(base64.b64decode(inline["data"]))

    return b"".join(pcm_chunks)


def _pcm_to_wav(pcm_data: bytes, output_path: Path, sample_rate: int):
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


def _adjust_tempo(wav_path: Path, slot_duration: float, max_speed: float, sample_rate: int):
    if slot_duration <= 0:
        return

    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.getnframes()
        tts_duration = frames / wf.getframerate()

    if tts_duration <= slot_duration:
        return

    speed = min(tts_duration / slot_duration, max_speed)
    tmp_path = wav_path.with_suffix(".tmp.wav")

    filters = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    filters.append(f"atempo={remaining:.4f}")

    cmd = [
        "ffmpeg", "-y", "-i", str(wav_path),
        "-filter:a", ",".join(filters),
        str(tmp_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    tmp_path.replace(wav_path)
