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

## Requirements

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/download.html) in PATH
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) in PATH
- [Gemini API key](https://aistudio.google.com/apikey)

## Installation

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
pip install -r requirements.txt
cp config.yaml.example config.yaml
# Edit config.yaml — add your Gemini API key
```

Or set via environment variable:

```bash
export GEMINI_API_KEY="your-key-here"
```

## Quick Start

```bash
# Create a new project
python -m src.cli init "My Drama" --source zh --target id

# Add video URLs to projects/my-drama/links.txt

# Dub all episodes
python -m src.cli dub --project my-drama

# Or dub a single episode
python -m src.cli dub --project my-drama --episode 1
```

## Pipeline

```
Video → Download → Demucs (separate vocals) → Gemini (identify + translate) → TTS → Mix → Dubbed Video
```

| Step | Description | Tool |
|------|-------------|------|
| Download | Fetch video + extract audio | yt-dlp, ffmpeg |
| Separate | Split vocals from background | Demucs |
| Identify | Transcribe + speaker ID + translate + emotion | Gemini API |
| TTS | Text-to-speech per character | Gemini Live WebSocket |
| Mix | Place TTS at timestamps + mux with video | ffmpeg |

## Transcription Modes

Set in `config.yaml` under `transcription.source`:

| Mode | Description | Best for |
|------|-------------|----------|
| `subtitle` | Read hardcoded subtitles from video frames | Videos with burned-in subtitles |
| `video` | Transcribe from audio + visual cues | Videos without subtitles |
| `audio` | Transcribe from voice only | Audio-only content |

## CLI Commands

```bash
# Project management
python -m src.cli init "Title" --source zh --target id
python -m src.cli projects

# Full pipeline
python -m src.cli dub --project slug --episode 1
python -m src.cli dub --project slug                    # all episodes

# Individual steps
python -m src.cli identify --project slug --episode 1
python -m src.cli tts --project slug --episode 1
python -m src.cli tts --project slug --episode 1 --character "Name"
python -m src.cli mix --project slug --episode 1

# Character management
python -m src.cli characters --project slug
python -m src.cli characters --project slug --add "Name" --voice Puck --gender male

# Preview (identify only, no TTS)
python -m src.cli preview --project slug --episode 1
```

## Configuration

```yaml
# config.yaml
gemini_api_key: "your-key"

models:
  transcribe: "gemini-3-flash-preview"
  tts: "gemini-3.1-flash-live-preview"

audio:
  sample_rate: 24000
  max_speed: 1.5
  bg_volume: 1.0
  dub_volume: 0.7

transcription:
  source: "subtitle"   # or "video" or "audio"
```

## Available TTS Voices

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

See `config.yaml.example` for the full list of 28 voices.

## Project Structure

```
DubbingTTS/
├── config.yaml              # Global config
├── projects/
│   └── {slug}/
│       ├── project.yaml     # Language settings
│       ├── characters.yaml  # Character database (persistent)
│       ├── links.txt        # Episode URLs
│       ├── cache/ep{N}/     # Intermediate files
│       └── output/          # Final dubbed videos
└── src/
    ├── cli.py               # CLI entry point
    ├── downloader.py        # Download + demucs separation
    ├── character_id.py      # Gemini transcription + identification
    ├── tts_engine.py        # Gemini Live TTS
    ├── mixer.py             # Audio mixing + video muxing
    └── utils.py             # Shared helpers
```

## License

MIT
