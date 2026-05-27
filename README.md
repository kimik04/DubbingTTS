# DubbingTTS

Automated multi-language video dubbing bot. Converts video from any source language (Chinese, English, Korean, Japanese, etc.) to any target language (Indonesian, English, etc.) using Gemini AI for transcription, character identification, translation, and text-to-speech.

## Features

- Multi-project support — each title/drama is an independent project
- Multi-language — configurable source and target language per project
- Three transcription modes: subtitle (hardcoded), video (audio+visual), audio-only
- Per-character TTS via Gemini Live API with voice consistency
- Emotion detection — TTS speaks with appropriate emotion (happy, sad, angry)
- Duration-aware TTS — speech pace matches original dialogue timing
- Character persistence across episodes with auto-detection
- Full caching — resume from any step
- Cross-platform — Windows, macOS, Linux

## Installation

### Auto Setup (Recommended)

The setup script auto-detects your OS and installs all dependencies:

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
python setup.py
```

This will install ffmpeg, yt-dlp, and Python dependencies automatically using your system's package manager (scoop/choco/winget on Windows, brew on macOS, apt/dnf/pacman on Linux).

### Manual Setup

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
pip install -r requirements.txt
cp config.yaml.example config.yaml
```

Install ffmpeg and yt-dlp manually:

| OS | Command |
|----|---------|
| Windows | `scoop install ffmpeg yt-dlp` or `choco install ffmpeg yt-dlp` |
| macOS | `brew install ffmpeg yt-dlp` |
| Linux | `sudo apt install ffmpeg` + `pip install yt-dlp` |

### Configuration

Edit `config.yaml` and add your Gemini API key:

```yaml
gemini_api_key: "your-gemini-api-key-here"
```

Get a free API key at https://aistudio.google.com/apikey

Or set via environment variable:

```bash
# macOS/Linux
export GEMINI_API_KEY="your-key-here"

# Windows PowerShell
$env:GEMINI_API_KEY="your-key-here"
```

## Usage

### 1. Create a Project (Auto)

Just paste an episode 1 URL — the bot auto-detects the title and scrapes all episode links:

```bash
python -m src.cli auto "https://www.reelshort.com/id/episodes/episode-1-senyum-manis-di-bibirnya-695f4e3f97c459a97700cc5f-f2vwi23a98" --source zh --target id
```

Output:
```
Title: Senyum Manis di Bibirnya
Project: senyum-manis-di-bibirnya
Episodes: 62

Run: python -m src.cli dub --project senyum-manis-di-bibirnya --episode 1
```

Currently supports auto-scraping for ReelShort. For other platforms, use manual setup.

### 1b. Create a Project (Manual)

```bash
python -m src.cli init "Raja Judi Tanpa Mahkota" --source zh --target id
```

Then edit `projects/your-project/links.txt`:

```
# One URL per line, order = episode number
https://www.reelshort.com/id/episodes/episode-1-...
https://www.reelshort.com/id/episodes/episode-2-...
https://www.reelshort.com/id/episodes/episode-3-...
```

Supports: ReelShort, YouTube, direct MP4 URLs, or local file paths.

### 2. Run Dubbing

```bash
# Dub a single episode
python -m src.cli dub --project senyum-manis-di-bibirnya --episode 1

# Dub a range of episodes
python -m src.cli dub --project senyum-manis-di-bibirnya --episode 3-10

# Dub all episodes
python -m src.cli dub --project senyum-manis-di-bibirnya

# Dub from a specific URL (auto-assigns next episode number)
python -m src.cli dub --project senyum-manis-di-bibirnya --url "https://..."

# Dub + burn translated subtitle (blur original subtitle + overlay new one)
python -m src.cli dub --project senyum-manis-di-bibirnya --episode 1 --subtitle

# Dub without background music (voices only)
python -m src.cli dub --project senyum-manis-di-bibirnya --episode 1 --no-bg
```

Output video will be at `projects/your-project/output/ep1_dubbed.mp4`.

### 3. Merge Episodes

Combine multiple dubbed episodes into one video:

```bash
# Merge specific range
python -m src.cli merge --project senyum-manis-di-bibirnya --episode 1-10

# Merge all dubbed episodes
python -m src.cli merge --project senyum-manis-di-bibirnya

# Custom output filename
python -m src.cli merge --project senyum-manis-di-bibirnya --episode 1-5 --output "part1.mp4"
```

Output: `projects/your-project/output/ep1-10_dubbed.mp4` (or `full_dubbed.mp4` for all).

### 4. Re-run or Fix Specific Steps

If something goes wrong, you can re-run individual steps:

```bash
# Re-identify characters (re-upload video to Gemini)
python -m src.cli identify --project slug --episode 1 --force

# Re-generate TTS for all characters
python -m src.cli tts --project slug --episode 1 --force

# Re-generate TTS for one character only
python -m src.cli tts --project slug --episode 1 --character "Yosa Leostra" --force

# Re-mix audio (if you changed audio settings)
python -m src.cli mix --project slug --episode 1 --force

# Re-mix without background music (voices only)
python -m src.cli mix --project slug --episode 1 --force --no-bg

# Burn translated subtitle on already-dubbed video
python -m src.cli subtitle --project slug --episode 1
python -m src.cli subtitle --project slug --episode 3-10
```

The `subtitle` command will:
- Blur the original subtitle area (per-segment, width adapts to translation length)
- Overlay the translated text using ASS pixel-positioning
- Replace `output/ep{N}_dubbed.mp4` with the subtitled version
- Skip if already applied (tracked via `.subtitled` marker)

Tune appearance in `config.yaml` under the `subtitle:` section (font, size, y position, blur strip dimensions, etc.).

### 5. Manage Characters

```bash
# List all characters in a project
python -m src.cli characters --project slug

# Manually add a character with specific voice
python -m src.cli characters --project slug --add "Kakek" --voice Gacrux --gender male
```

### 6. Preview (No TTS)

Preview runs identify only — useful to check if transcription and translation are correct before generating TTS:

```bash
python -m src.cli preview --project slug --episode 1
```

### 7. List Projects

```bash
python -m src.cli projects
```

## Pipeline

```
Video → Download → Demucs (separate vocals) → Gemini (identify + translate) → TTS → Mix → Dubbed Video
```

| Step | What it does | Output |
|------|-------------|--------|
| Download | Fetch video via yt-dlp, extract audio | `cache/ep1/video.mp4`, `audio.mp3` |
| Separate | Demucs splits vocals from background music | `cache/ep1/vocals.wav`, `no_vocals.wav` |
| Identify | Gemini watches video, identifies speakers, translates | `cache/ep1/identified_segments.json` |
| TTS | Gemini Live generates speech per character | `cache/ep1/tts/{Character}/seg_XXXX.wav` |
| Mix | ffmpeg places TTS at timestamps, mixes with background | `output/ep1_dubbed.mp4` |

## Transcription Modes

Set in `config.yaml` under `transcription.source`:

| Mode | Description | Best for |
|------|-------------|----------|
| `subtitle` | Read hardcoded subtitles from video frames, timestamp = subtitle appearance | Videos with burned-in subtitles (most accurate text) |
| `video` | Transcribe from audio + visual cues, timestamp = when speech is heard | Videos without subtitles |
| `audio` | Transcribe from voice only (no video upload needed) | Audio-only content or saving API quota |

## Configuration Reference

```yaml
# config.yaml

# Gemini API key (or set GEMINI_API_KEY env var)
gemini_api_key: "your-key"

# Models
models:
  transcribe: "gemini-3-flash-preview"       # For video/audio analysis
  tts: "gemini-3.1-flash-live-preview"       # For TTS via Live API

# Audio settings
audio:
  sample_rate: 24000    # TTS output sample rate (Hz)
  max_speed: 1.5        # Max atempo speedup for TTS that exceeds slot duration
  bg_volume: 1.0        # Background music volume in final mix
  dub_volume: 0.7       # Dubbed voice volume in final mix

# Transcription mode
transcription:
  source: "subtitle"    # "subtitle", "video", or "audio"

# Demucs settings
demucs:
  model: "htdemucs"     # Demucs model for vocal separation
  two_stems: true       # Separate into vocals + no_vocals only
```

## Available TTS Voices

All voices are from Gemini Live API. Use these exact names in `characters.yaml`:

| Male | Female |
|------|--------|
| Puck | Aoede |
| Charon | Kore |
| Fenrir | Zephyr |
| Orus | Leda |
| Enceladus | Callirrhoe |
| Iapetus | Autonoe |
| Algenib | Despina |
| Rasalgethi | Erinome |
| Alnilam | Laomedeia |
| Schedar | Achernar |
| Gacrux | Pulcherrima |
| Achird | Vindemiatrix |
| Zubenelgenubi | Sadachbia |
| Sadaltager | Sulafat |

## Project Structure

```
DubbingTTS/
├── setup.py                 # Auto-setup script (detect OS, install deps)
├── config.yaml              # Global config (API key, models, audio settings)
├── config.yaml.example      # Template config
├── requirements.txt         # Python dependencies
├── projects/
│   └── {slug}/
│       ├── project.yaml     # Language settings (source/target)
│       ├── characters.yaml  # Character database (voice, gender, persistent)
│       ├── links.txt        # Episode video URLs (one per line)
│       ├── cache/
│       │   └── ep{N}/
│       │       ├── video.mp4
│       │       ├── audio.mp3
│       │       ├── vocals.wav
│       │       ├── no_vocals.wav
│       │       ├── identified_segments.json
│       │       └── tts/{Character}/seg_XXXX.wav
│       └── output/
│           └── ep{N}_dubbed.mp4
└── src/
    ├── cli.py               # CLI entry point
    ├── downloader.py        # Download video + demucs separation
    ├── character_id.py      # Gemini: transcribe + identify + translate
    ├── tts_engine.py        # Gemini Live API: text-to-speech
    ├── mixer.py             # ffmpeg: mix TTS + background + video
    └── utils.py             # Shared helpers (config, segments, retry)
```

## Supported Languages

**Source** (what language the video is in):
zh, en, ko, ja, th, es, fr, de, ru, ar, and any language Gemini can understand

**Target** (what language to dub into):
id, en, zh, ms, th, and any language Gemini TTS can speak

## Troubleshooting

| Problem | Solution |
|---------|----------|
| 429 Too Many Requests | Wait 1-2 minutes, retry. Free tier = 5 RPM |
| 1011 Internal Error on TTS | Voice name invalid or server overloaded. Check voice list above |
| TTS too fast/slow | Adjust `audio.max_speed` in config (1.0-2.0) |
| Dubbing not synced | Try `transcription.source: "subtitle"` for videos with hardcoded subs |
| Characters not consistent across episodes | Check `characters.yaml` — names must match exactly |
| Demucs fails | Install `soundfile`: `pip install soundfile` |

## License

MIT
