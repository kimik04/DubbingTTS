from __future__ import annotations

import asyncio
import base64
import json
import logging
import subprocess
import wave
from pathlib import Path

import requests
import websockets

from .utils import (
    PROJECT_ROOT, Segment, get_cache_dir, get_output_dir,
    load_project_config, load_global_config, save_segments, retry,
)

log = logging.getLogger(__name__)

BGM_DIR = PROJECT_ROOT / "bgm"

MAX_RETRIES = 3
SPEED_MIN = 0.8
SPEED_MAX = 1.3


def narrate_episode_sync(slug: str, episode: int, speed: float | None = None, bgm: str | None = None, force: bool = False) -> Path:
    return asyncio.run(narrate_episode(slug, episode, speed, bgm, force))


async def narrate_episode(slug: str, episode: int, speed: float | None = None, bgm: str | None = None, force: bool = False) -> Path:
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
    narration_cfg = project.get("narration", {})

    api_key = global_config["gemini_api_key"]
    model_transcribe = global_config["models"]["transcribe"]
    model_tts = global_config["models"]["tts"]
    sample_rate = global_config["audio"]["sample_rate"]
    target_lang = project["language"]["target"]

    voice = narration_cfg.get("voice", "Kore")
    narration_volume = narration_cfg.get("narration_volume", 1.0)
    bgm_volume = narration_cfg.get("bgm_volume", 0.3)
    speed_override = speed or narration_cfg.get("speed", None)
    bgm_source = bgm or narration_cfg.get("bgm", None)

    video_duration = _get_video_duration(video_path)

    # Step 1: Generate narration text
    log.info(f"ep{episode}: generating narration text")
    narration_text = _generate_narration_text(
        video_path, target_lang, video_duration, api_key, model_transcribe, cache, force
    )
    log.info(f"ep{episode}: narration text ({len(narration_text)} chars)")

    # Step 2: TTS narration + fit to duration
    log.info(f"ep{episode}: generating narration audio (voice={voice})")
    narration_wav = cache / "narration.wav"
    sentence_durations = await _generate_narration_audio(
        narration_text, narration_wav, voice, target_lang,
        video_duration, speed_override, sample_rate, api_key, model_tts, cache, force
    )

    # Step 3: Get BGM
    bgm_path = _resolve_bgm(bgm_source, cache)

    # Step 4: Mix narration + BGM
    log.info(f"ep{episode}: mixing narration + BGM")
    final_audio = cache / "narration_final.mp3"
    _mix_narration(narration_wav, bgm_path, final_audio, narration_volume, bgm_volume, video_duration)

    # Step 5: Mux with video
    log.info(f"ep{episode}: muxing video")
    _mux_video(video_path, final_audio, output_path)

    # Step 6: Auto-subtitle (using real TTS durations)
    log.info(f"ep{episode}: burning subtitles")
    segments = _durations_to_segments(sentence_durations)
    save_segments(segments, cache / "identified_segments.json")
    _burn_subtitles(output_path, segments, project)

    log.info(f"ep{episode}: done -> {output_path}")
    return output_path


@retry()
def _generate_narration_text(video_path: Path, target_lang: str, duration: float, api_key: str, model: str, cache: Path, force: bool) -> str:
    text_path = cache / "narration_text.txt"
    if not force and text_path.exists():
        return text_path.read_text(encoding="utf-8")

    lang_names = {"zh": "Chinese", "en": "English", "ko": "Korean", "ja": "Japanese", "id": "Indonesian", "th": "Thai", "ms": "Malay"}
    target_name = lang_names.get(target_lang, target_lang)

    words_per_sec = 2.5
    target_words = int(duration * words_per_sec)

    prompt = f"""Watch this video carefully. It contains narration/voiceover in a foreign language.

Your task: Write a NEW narration script in {target_name} that tells the same story/content.

Requirements:
- Write natural, flowing narration — NOT a literal translation, NOT subtitles
- The narration should sound like a professional voiceover narrator
- Target approximately {target_words} words to fill {duration:.0f} seconds of audio
- Match the pacing to the visual content — describe what's happening on screen
- Keep the tone and mood consistent with the video
- Write as one continuous text, no timestamps or segment markers
- Use natural pauses (commas, periods) where the narrator would breathe

Output ONLY the narration text, nothing else."""

    with open(video_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode()

    payload = {
        "model": model,
        "input": [
            {"type": "video", "data": b64_data, "mime_type": "video/mp4"},
            {"type": "text", "text": prompt},
        ],
        "generation_config": {"thinking_level": "high"},
        "store": False,
    }

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    resp = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        json=payload, headers=headers, timeout=300
    )
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
        raise RuntimeError(f"No text output in response")

    text_path.write_text(text_output, encoding="utf-8")
    return text_output


async def _generate_narration_audio(
    text: str, output_path: Path, voice: str, target_lang: str,
    video_duration: float, speed_override: float | None,
    sample_rate: int, api_key: str, model: str, cache: Path, force: bool
) -> list[tuple[str, float]]:
    """Returns list of (sentence_text, final_duration_seconds) for subtitle sync."""
    raw_wav = cache / "narration_raw.wav"

    if not force and raw_wav.exists():
        log.info("  using cached raw narration audio")
        chunk_durations = _load_chunk_durations(cache)
    else:
        chunk_pcms, chunk_texts = await _tts_narration(text, voice, target_lang, api_key, model)
        all_pcm = b"".join(chunk_pcms)
        _pcm_to_wav(all_pcm, raw_wav, sample_rate)
        chunk_durations = [(t, len(p) / (sample_rate * 2)) for t, p in zip(chunk_texts, chunk_pcms)]
        _save_chunk_durations(cache, chunk_durations)

    tts_duration = _get_audio_duration(raw_wav)
    ratio = video_duration / tts_duration if tts_duration > 0 else 1.0
    log.info(f"  TTS duration: {tts_duration:.1f}s, video: {video_duration:.1f}s, ratio: {ratio:.2f}")

    if speed_override:
        atempo = speed_override
        log.info(f"  using manual speed override: {atempo}")
    elif SPEED_MIN <= ratio <= SPEED_MAX:
        atempo = ratio
        log.info(f"  auto-fit atempo: {atempo:.2f}")
    else:
        log.warning(f"  ratio {ratio:.2f} outside {SPEED_MIN}-{SPEED_MAX}, re-generating text")
        new_text = _regenerate_text(text, ratio, video_duration, cache)
        chunk_pcms, chunk_texts = await _tts_narration(new_text, voice, target_lang, api_key, model)
        all_pcm = b"".join(chunk_pcms)
        _pcm_to_wav(all_pcm, raw_wav, sample_rate)
        chunk_durations = [(t, len(p) / (sample_rate * 2)) for t, p in zip(chunk_texts, chunk_pcms)]
        _save_chunk_durations(cache, chunk_durations)
        tts_duration = _get_audio_duration(raw_wav)
        ratio = video_duration / tts_duration if tts_duration > 0 else 1.0
        atempo = max(SPEED_MIN, min(SPEED_MAX, ratio))
        log.info(f"  after re-gen: TTS {tts_duration:.1f}s, ratio {ratio:.2f}, atempo {atempo:.2f}")

    if abs(atempo - 1.0) > 0.02:
        _apply_atempo(raw_wav, output_path, atempo)
    else:
        import shutil
        shutil.copy2(raw_wav, output_path)

    final_dur = _get_audio_duration(output_path)
    if final_dur < video_duration:
        _pad_silence(output_path, video_duration, sample_rate)

    scale = 1.0 / atempo if abs(atempo - 1.0) > 0.02 else 1.0
    return [(t, d * scale) for t, d in chunk_durations]


def _regenerate_text(original_text: str, ratio: float, duration: float, cache: Path) -> str:
    if ratio < SPEED_MIN:
        instruction = "Make it SHORTER. Remove less important details, be more concise."
        target_factor = 0.75
    else:
        instruction = "Make it LONGER. Add more descriptive detail, elaborate on scenes."
        target_factor = 1.3

    target_words = int(duration * 2.5 * target_factor)
    current_words = len(original_text.split())

    log.info(f"  re-generating: current ~{current_words} words, target ~{target_words} words")

    new_text = original_text
    if ratio < SPEED_MIN:
        sentences = original_text.replace("。", ".").replace("，", ",").split(".")
        keep = int(len(sentences) * 0.7)
        new_text = ". ".join(sentences[:keep]) + "."
    else:
        new_text = original_text + " " + original_text[:int(len(original_text) * 0.3)]

    text_path = cache / "narration_text.txt"
    text_path.write_text(new_text, encoding="utf-8")
    return new_text


async def _tts_narration(text: str, voice: str, target_lang: str, api_key: str, model: str) -> tuple[list[bytes], list[str]]:
    """Returns (list_of_pcm_per_sentence, list_of_sentence_texts)."""
    import re
    lang_names = {"id": "Indonesian", "en": "English", "zh": "Chinese", "ms": "Malay", "th": "Thai"}
    target_name = lang_names.get(target_lang, target_lang)

    sentences = re.split(r'(?<=[.!?。！？])\s*', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    ws = await _connect_ws(api_key, model)
    try:
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
                    "parts": [{"text": f"You are a professional narrator. Speak in {target_name}. Read the following narration script naturally, with good pacing and clear pronunciation. Use a warm, engaging narrator tone."}]
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

        all_pcm = []
        for i, sentence in enumerate(sentences):
            log.info(f"  TTS sentence {i+1}/{len(sentences)} ({len(sentence)} chars)")
            msg = {
                "clientContent": {
                    "turns": [{"role": "user", "parts": [{"text": sentence}]}],
                    "turnComplete": True,
                }
            }
            await ws.send(json.dumps(msg))

            pcm_chunks = []
            while True:
                resp = await asyncio.wait_for(ws.recv(), timeout=60)
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

            all_pcm.append(b"".join(pcm_chunks))
            await asyncio.sleep(1)

        return all_pcm, sentences
    finally:
        try:
            await ws.close()
        except:
            pass


async def _connect_ws(api_key: str, model: str):
    url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"
    return await websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=10)


def _split_text(text: str, max_chars: int = 800) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks = []
    sentences = text.replace("\n", " ").split(". ")
    current = ""

    for sentence in sentences:
        candidate = (current + ". " + sentence).strip(". ") if current else sentence
        if len(candidate) > max_chars and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = candidate

    if current.strip():
        chunks.append(current.strip())

    return chunks


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


def _mix_narration(narration_path: Path, bgm_path: Path, output: Path, narration_vol: float, bgm_vol: float, duration: float):
    cmd = [
        "ffmpeg", "-y",
        "-i", str(narration_path),
        "-i", str(bgm_path),
        "-filter_complex",
        f"[0:a]volume={narration_vol}[nar];[1:a]volume={bgm_vol}[bg];[nar][bg]amix=inputs=2:duration=longest:normalize=0",
        "-t", str(duration),
        str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


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


def _get_video_duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def _get_audio_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _pcm_to_wav(pcm_data: bytes, output_path: Path, sample_rate: int):
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


def _apply_atempo(input_path: Path, output_path: Path, atempo: float):
    filters = []
    remaining = atempo
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-filter:a", ",".join(filters),
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _pad_silence(wav_path: Path, target_duration: float, sample_rate: int):
    current = _get_audio_duration(wav_path)
    if current >= target_duration:
        return

    padded = wav_path.with_suffix(".padded.wav")
    cmd = [
        "ffmpeg", "-y", "-i", str(wav_path),
        "-af", f"apad=whole_dur={target_duration}",
        str(padded),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    padded.replace(wav_path)


def _save_chunk_durations(cache: Path, durations: list[tuple[str, float]]):
    path = cache / "narration_durations.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(durations, f, ensure_ascii=False)


def _load_chunk_durations(cache: Path) -> list[tuple[str, float]]:
    path = cache / "narration_durations.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [tuple(x) for x in json.load(f)]


def _durations_to_segments(sentence_durations: list[tuple[str, float]]) -> list[Segment]:
    segments = []
    current_time = 0.0

    for i, (text, dur) in enumerate(sentence_durations):
        if not text.strip():
            continue
        start = current_time
        end = current_time + dur
        current_time = end

        m_s, s_s = divmod(int(start), 60)
        m_e, s_e = divmod(int(end), 60)

        segments.append(Segment(
            index=i,
            start=f"{m_s:02d}:{s_s:02d}",
            end=f"{m_e:02d}:{s_e:02d}",
            text=text,
            character="Narrator",
            translation=text,
        ))

    return segments


def _burn_subtitles(video_path: Path, segments: list[Segment], project: dict):
    from .subtitler import _auto_config, _video_size, _write_ass, _render

    width, height = _video_size(video_path)
    cfg = {**_auto_config(width, height), **(project.get("subtitle") or {})}

    cache = video_path.parent.parent / "cache" / video_path.stem.replace("_narrated", "").replace("ep", "ep")
    if not cache.exists():
        cache = video_path.parent

    ass_path = video_path.with_suffix(".ass")
    _write_ass(ass_path, segments, width, height, cfg)

    import shutil
    temp = video_path.with_suffix(".tmp.mp4")
    _render(video_path, ass_path, segments, width, temp, cfg)
    shutil.move(str(temp), str(video_path))
    ass_path.unlink(missing_ok=True)
