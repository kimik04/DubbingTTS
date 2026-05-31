from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import subprocess
import wave
from pathlib import Path

import requests
import websockets

from .utils import (
    PROJECT_ROOT, Segment, get_cache_dir, get_output_dir,
    load_project_config, load_global_config, load_segments, save_segments, retry,
)

log = logging.getLogger(__name__)

BGM_DIR = PROJECT_ROOT / "bgm"

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "content": {"type": "string"},
                    "translation": {"type": "string"},
                },
                "required": ["timestamp", "content", "translation"]
            }
        }
    },
    "required": ["segments"]
}


def narrate_episode_sync(slug: str, episode: int, voice: str = "Kore", speed: float | None = None, bgm: str | None = None, force: bool = False) -> Path:
    return asyncio.run(narrate_episode(slug, episode, voice, speed, bgm, force))


async def narrate_episode(slug: str, episode: int, voice: str = "Kore", speed: float | None = None, bgm: str | None = None, force: bool = False) -> Path:
    cache = get_cache_dir(slug, episode)
    output_dir = get_output_dir(slug)
    output_path = output_dir / f"ep{episode}_narrated.mp4"

    if not force and output_path.exists():
        log.info(f"ep{episode}: narration cached, skipping")
        return output_path

    video_path = cache / "video.mp4"
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}. Run download first.")

    project = load_project_config(slug)
    global_config = load_global_config()

    api_key = global_config["gemini_api_key"]
    model_transcribe = global_config["models"]["transcribe"]
    model_tts = global_config["models"]["tts"]
    sample_rate = global_config["audio"]["sample_rate"]
    max_speed = global_config["audio"].get("max_speed", 1.5)
    target_lang = project["language"]["target"]

    narration_volume = 1.0
    bgm_volume = 0.3
    bgm_source = bgm
    speed_val = speed

    # Step 0: Speed-adjust video if requested (before Gemini sees it)
    if speed_val and speed_val != 1.0:
        video_path = _speed_video(video_path, speed_val, cache, force)
        log.info(f"ep{episode}: video speed adjusted to {speed_val}x")

    # Step 1: Transcribe + translate (segment-based like dubbing)
    segments = _identify_narration(video_path, target_lang, api_key, model_transcribe, cache, force)
    log.info(f"ep{episode}: {len(segments)} narration segments")
    save_segments(segments, cache / "identified_segments.json")

    # Step 2: TTS per-segment (single voice)
    log.info(f"ep{episode}: TTS narration (voice={voice})")
    tts_dir = cache / "tts" / "Narrator"
    tts_dir.mkdir(parents=True, exist_ok=True)
    await _tts_segments(segments, voice, target_lang, tts_dir, sample_rate, max_speed, api_key, model_tts, force)

    # Step 3: Place TTS on timeline + mix with BGM
    video_duration = _get_video_duration(video_path)
    bgm_path = _resolve_bgm(bgm_source, cache)

    log.info(f"ep{episode}: placing segments on timeline")
    dub_raw = cache / "narration_raw.wav"
    _place_tts_segments(segments, tts_dir, video_duration, sample_rate, dub_raw)

    log.info(f"ep{episode}: mixing with BGM")
    final_audio = cache / "narration_final.mp3"
    _mix_audio(dub_raw, bgm_path, final_audio, narration_volume, bgm_volume, video_duration)

    # Step 4: Mux video
    log.info(f"ep{episode}: muxing video")
    _mux_video(video_path, final_audio, output_path)

    # Step 5: Burn subtitles
    log.info(f"ep{episode}: burning subtitles")
    _burn_subtitles(output_path, segments, project)

    log.info(f"ep{episode}: done -> {output_path}")
    return output_path


def _identify_narration(video_path: Path, target_lang: str, api_key: str, model: str, cache: Path, force: bool) -> list[Segment]:
    segments_path = cache / "identified_segments.json"
    if not force and segments_path.exists():
        log.info("  using cached segments")
        return load_segments(segments_path)

    lang_names = {"zh": "Chinese", "en": "English", "ko": "Korean", "ja": "Japanese", "id": "Indonesian", "th": "Thai", "ms": "Malay"}
    target_name = lang_names.get(target_lang, target_lang)

    prompt = f"""Watch and listen to this video carefully. It contains narration/voiceover.

Your task: Transcribe the narration into segments and translate each segment into {target_name}.

Requirements:
- Each segment = one sentence or natural phrase from the narrator
- Provide accurate timestamps (MM:SS) for when each segment is spoken
- Transcribe the original narration in "content"
- Translate naturally into {target_name} in "translation" — make it sound like a professional narrator
- Keep translations concise and speakable in roughly the same duration
- Do NOT merge multiple sentences. Each spoken sentence = one segment.
- Do NOT skip any narration."""

    result = _call_gemini(video_path, prompt, api_key, model)
    return _parse_segments(result)


@retry()
def _call_gemini(video_path: Path, prompt: str, api_key: str, model: str) -> dict:
    with open(video_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode()

    payload = {
        "model": model,
        "input": [
            {"type": "video", "data": b64_data, "mime_type": "video/mp4"},
            {"type": "text", "text": prompt},
        ],
        "generation_config": {"thinking_level": "high"},
        "response_format": {"type": "text", "mime_type": "application/json", "schema": RESPONSE_SCHEMA},
        "store": False,
    }

    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    resp = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        json=payload, headers=headers, timeout=300
    )
    if resp.status_code != 200:
        log.error(f"  API error {resp.status_code}: {resp.text[:500]}")
    resp.raise_for_status()

    data = resp.json()
    for out in data.get("outputs", []):
        if out.get("type") == "text":
            return json.loads(out["text"])

    for step in data.get("steps", []):
        for out in step.get("outputs", []):
            if out.get("type") == "text":
                return json.loads(out["text"])

    raise RuntimeError(f"No text output in response. Keys: {list(data.keys())}")


def _normalize_ts(ts: str) -> str:
    import re
    ts = ts.strip()
    # Handle range "00:03-00:05" — first part already has colon, take it
    range_match = re.match(r'(\d+:\d+(?::\d+)?)\s*[-–—]\s*\d', ts)
    if range_match:
        ts = range_match.group(1)
    # Handle "00 - 00" (no colons, dash as MM:SS separator) → "00:00"
    elif re.match(r'^\d+\s*[-–—]\s*\d+$', ts):
        ts = re.sub(r'\s*[-–—]\s*', ':', ts)
    ts = re.sub(r'\s+', ':', ts)
    return ts


def _parse_segments(result: dict) -> list[Segment]:
    from .utils import parse_timestamp

    raw = result.get("segments", [])
    segments = []
    GAP = 0.3

    for i, seg in enumerate(raw):
        start_ts = _normalize_ts(seg["timestamp"])
        if i + 1 < len(raw):
            next_ts = _normalize_ts(raw[i + 1]["timestamp"])
            next_sec = parse_timestamp(next_ts)
        else:
            next_ts = start_ts
            next_sec = None

        start_sec = parse_timestamp(start_ts)
        end_sec = parse_timestamp(next_ts) if next_sec is not None else start_sec + 4.0
        slot = end_sec - start_sec

        if slot > 10.0:
            end_sec = start_sec + 5.0
        elif slot <= 0:
            end_sec = start_sec + 4.0

        end_sec = end_sec - GAP if end_sec - GAP > start_sec else end_sec

        m, s = divmod(int(end_sec), 60)
        end_ts = f"{m:02d}:{s:02d}"

        segments.append(Segment(
            index=i,
            start=start_ts,
            end=end_ts,
            text=seg.get("content", ""),
            character="Narrator",
            translation=seg.get("translation", seg.get("content", "")),
        ))

    return segments


async def _tts_segments(segments: list[Segment], voice: str, target_lang: str, tts_dir: Path, sample_rate: int, max_speed: float, api_key: str, model: str, force: bool):
    lang_names = {"id": "Indonesian", "en": "English", "zh": "Chinese", "ms": "Malay", "th": "Thai"}
    target_name = lang_names.get(target_lang, target_lang)

    pending = []
    for seg in segments:
        if not seg.translation:
            continue
        wav_path = tts_dir / f"seg_{seg.index:04d}.wav"
        if not force and wav_path.exists():
            continue
        pending.append((seg, wav_path))

    if not pending:
        log.info("  all TTS cached")
        return

    log.info(f"  generating {len(pending)} segments")

    ws = await _connect_ws(api_key, model)
    try:
        await _send_setup(ws, voice, target_name, model)

        for seg, wav_path in pending:
            slot_duration = seg.end_sec - seg.start_sec
            for attempt in range(3):
                try:
                    pcm = await _generate_one(ws, seg.translation, slot_duration)
                    if not pcm:
                        raise RuntimeError("Empty audio")
                    _pcm_to_wav(pcm, wav_path, sample_rate)
                    _adjust_tempo(wav_path, slot_duration, max_speed, sample_rate)
                    break
                except Exception as e:
                    if attempt == 2:
                        log.error(f"  seg_{seg.index}: failed ({e})")
                        break
                    log.warning(f"  seg_{seg.index}: retry ({e})")
                    try:
                        await ws.close()
                    except:
                        pass
                    await asyncio.sleep(10)
                    ws = await _connect_ws(api_key, model)
                    await _send_setup(ws, voice, target_name, model)
            await asyncio.sleep(1)
    finally:
        try:
            await ws.close()
        except:
            pass


async def _connect_ws(api_key: str, model: str):
    url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"
    return await websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=10)


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
                "parts": [{"text": f"You are a professional narrator. Speak in {target_lang}. Read naturally with good pacing and clear pronunciation. Use a warm, engaging narrator tone."}]
            },
            "contextWindowCompression": {"slidingWindow": {}},
            "sessionResumption": {}
        }
    }
    await ws.send(json.dumps(setup_msg))
    resp = await asyncio.wait_for(ws.recv(), timeout=15)
    data = json.loads(resp)
    if "setupComplete" not in data:
        raise RuntimeError(f"Setup failed: {data}")


async def _generate_one(ws, text: str, slot_duration: float) -> bytes:
    if slot_duration > 0:
        prompt = f"[{slot_duration:.0f} detik] {text}"
    else:
        prompt = text

    msg = {
        "clientContent": {
            "turns": [{"role": "user", "parts": [{"text": prompt}]}],
            "turnComplete": True,
        }
    }
    await ws.send(json.dumps(msg))

    pcm_chunks = []
    while True:
        resp = await asyncio.wait_for(ws.recv(), timeout=30)
        data = json.loads(resp)

        if "goAway" in data:
            raise RuntimeError("Session GoAway")

        server_content = data.get("serverContent")
        if not server_content:
            continue
        if server_content.get("turnComplete"):
            break
        if server_content.get("interrupted"):
            break

        parts = server_content.get("modelTurn", {}).get("parts", [])
        for part in parts:
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                pcm_chunks.append(base64.b64decode(inline["data"]))

    return b"".join(pcm_chunks)


def _place_tts_segments(segments: list[Segment], tts_dir: Path, duration: float, sample_rate: int, output: Path):
    silence = output.with_name("silence.wav")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", str(duration),
        str(silence),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    tts_files = []
    for seg in segments:
        wav = tts_dir / f"seg_{seg.index:04d}.wav"
        if wav.exists():
            tts_files.append((seg, wav))

    if not tts_files:
        shutil.copy2(silence, output)
        return

    inputs = ["-i", str(silence)]
    filter_parts = []

    for i, (seg, wav) in enumerate(tts_files):
        inputs.extend(["-i", str(wav)])
        delay_ms = int(seg.start_sec * 1000)
        filter_parts.append(f"[{i+1}]adelay={delay_ms}|{delay_ms}[d{i}]")

    mix_inputs = "[0]" + "".join(f"[d{i}]" for i in range(len(tts_files)))
    filter_parts.append(f"{mix_inputs}amix=inputs={len(tts_files)+1}:duration=first:dropout_transition=0:normalize=0")
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", filter_complex, str(output)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    silence.unlink(missing_ok=True)


def _mix_audio(dub_path: Path, bgm_path: Path, output: Path, dub_vol: float, bgm_vol: float, duration: float):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(dub_path),
        "-i", str(bgm_path),
        "-filter_complex",
        f"[0:a]volume={dub_vol}[nar];[1:a]volume={bgm_vol}[bg];[nar][bg]amix=inputs=2:duration=longest:normalize=0",
        "-t", str(duration),
        str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _resolve_bgm(bgm_source: str | None, cache: Path) -> Path:
    if bgm_source:
        bgm_path = BGM_DIR / bgm_source
        if bgm_path.exists():
            return bgm_path
        bgm_path = Path(bgm_source)
        if bgm_path.exists():
            return bgm_path
        raise FileNotFoundError(f"BGM not found: {bgm_source}. Check bgm/ folder.")

    no_vocals = cache / "no_vocals.wav"
    if no_vocals.exists():
        return no_vocals
    raise FileNotFoundError("no_vocals.wav not found. Run demucs separation first, or specify --bgm.")


def _mux_video(video_path: Path, audio_path: Path, output_path: Path):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac",
        "-map", "0:v:0", "-map", "1:a:0",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _burn_subtitles(video_path: Path, segments: list[Segment], project: dict):
    from .subtitler import _auto_config, _video_size, _write_ass, _render

    width, height = _video_size(video_path)
    cfg = {**_auto_config(width, height), **(project.get("subtitle") or {})}

    ass_path = video_path.with_suffix(".ass")
    _write_ass(ass_path, segments, width, height, cfg)

    temp = video_path.with_suffix(".tmp.mp4")
    _render(video_path, ass_path, segments, width, temp, cfg)
    shutil.move(str(temp), str(video_path))
    ass_path.unlink(missing_ok=True)


def _get_video_duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())



def _speed_video(video_path: Path, speed: float, cache: Path, force: bool) -> Path:
    output = cache / "video_speed.mp4"
    if not force and output.exists():
        return output

    pts = 1.0 / speed
    has_audio = _has_audio(video_path)

    if has_audio:
        atempo_filters = []
        remaining = speed
        while remaining > 2.0:
            atempo_filters.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            atempo_filters.append("atempo=0.5")
            remaining /= 0.5
        atempo_filters.append(f"atempo={remaining:.4f}")

        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-filter_complex",
            f"[0:v]setpts={pts:.4f}*PTS[v];[0:a]{','.join(atempo_filters)}[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            str(output),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", f"setpts={pts:.4f}*PTS",
            "-an",
            "-c:v", "libx264", "-preset", "fast",
            str(output),
        ]

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return output


def _has_audio(path: Path) -> bool:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return bool(result.stdout.strip())


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
