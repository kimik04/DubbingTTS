# DubbingTTS

Automated multi-language video dubbing bot. Converts video from any source language (Chinese, English, Korean, Japanese, etc.) to any target language (Indonesian, English, etc.) using AI transcription, character identification, and text-to-speech.

## Features

- Multi-project support — each title/drama is an independent project with its own characters and settings
- Multi-language — configurable source and target language per project
- Per-character TTS — maintains voice consistency by batching all segments per character in a single session
- Character persistence — character database carries across episodes, new characters auto-detected
- Full caching — every intermediate result is cached, pipeline can resume from any step
- Cross-platform — works on macOS (Apple Silicon optimized), Windows, and Linux

## Requirements

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/download.html) installed and in PATH
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) installed and in PATH (for URL downloads)
- [Gemini API key](https://aistudio.google.com/apikey)

## Installation

### macOS

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
pip install -r requirements.txt
```

### Windows

```powershell
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS

# Install ffmpeg and yt-dlp (pick one)
scoop install ffmpeg yt-dlp
# or: choco install ffmpeg yt-dlp

pip install -r requirements.txt
```

### Linux

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
sudo apt install ffmpeg  # or your distro's package manager
pip install -r requirements.txt
```

### Configuration

```bash
cp config.yaml.example config.yaml
# Edit config.yaml and add your Gemini API key
```

Or set via environment variable:

```bash
# macOS/Linux
export GEMINI_API_KEY="your-key-here"

# Windows PowerShell
$env:GEMINI_API_KEY="your-key-here"
```

## Quick Start

```bash
# Create a new project
python -m src.cli init "My Drama Title" --source zh --target id

# Add video URLs to projects/my-drama-title/links.txt

# Run full dubbing pipeline
python -m src.cli dub --project my-drama-title --episode 1
```

## Pipeline

```
Video → Download → Transcribe → Identify Characters → TTS → Mix → Dubbed Video
```

| Step | Description | Tool |
|------|-------------|------|
| 1. Download | Fetch video + extract audio | yt-dlp, ffmpeg |
| 2. Transcribe | Speech-to-text with timestamps | Whisper (MLX/OpenAI) |
| 3. Identify | Character ID + translation | Gemini API |
| 4. TTS | Text-to-speech per character | Gemini Live WebSocket |
| 5. Mix | Background separation + mux | Demucs, ffmpeg |

## CLI Commands

```bash
# Project management
python -m src.cli init "Title" --source zh --target id
python -m src.cli projects

# Full pipeline
python -m src.cli dub --project slug --episode 1
python -m src.cli dub --project slug                    # all episodes

# Individual steps
python -m src.cli transcribe --project slug --episode 1
python -m src.cli identify --project slug --episode 1
python -m src.cli tts --project slug --episode 1
python -m src.cli tts --project slug --episode 1 --character Adrian
python -m src.cli mix --project slug --episode 1

# Character management
python -m src.cli characters --project slug
python -m src.cli characters --project slug --add "Name" --voice Puck --gender male

# Preview (transcribe + identify only, no TTS)
python -m src.cli preview --project slug --episode 1
```

## Project Structure

```
DubbingTTS/
├── config.yaml              # Global config (API keys, models, audio settings)
├── projects/
│   └── {slug}/
│       ├── project.yaml     # Per-project config (language, scenes)
│       ├── characters.yaml  # Character database (persistent across episodes)
│       ├── links.txt        # Episode video URLs (one per line)
│       ├── cache/           # Intermediate files (auto-generated)
│       └── output/          # Final dubbed videos
└── src/
    ├── cli.py               # CLI entry point
    ├── downloader.py        # Video download + audio extraction
    ├── transcriber.py       # Whisper transcription
    ├── character_id.py      # Gemini character identification + translation
    ├── tts_engine.py        # Gemini Live TTS (per character)
    ├── mixer.py             # Audio mixing + video muxing
    └── utils.py             # Shared helpers
```

## Configuration

### Global (`config.yaml`)

```yaml
gemini_api_key: "your-key"

models:
  whisper: "mlx-community/whisper-small-mlx"
  transcribe: "gemini-3.1-flash-lite-preview"
  tts: "gemini-3.1-flash-live-preview"

audio:
  sample_rate: 24000
  max_speed: 2.0
  bg_volume: 1.0
  dub_volume: 0.7
```

### Per-project (`project.yaml`)

```yaml
title: "My Drama"
slug: "my-drama"

language:
  source: "zh"    # Whisper language code
  target: "id"    # Translation + TTS target

episodes:
  ep1:
    scenes:
      - time: "0:00-1:30"
        description: "Scene description for character identification context"
```

## Available Voices

| Male   | Female |
|--------|--------|
| Puck   | Aoede  |
| Charon | Kore   |
| Fenrir | Zephyr |
| Orus   | Elara  |
| Leda   | Vesta  |

## Supported Languages

**Source** (transcription): zh, en, ko, ja, th, es, fr, de, ru, ar, and 90+ more

**Target** (dubbing): id, en, zh, ms, th, and any language supported by Gemini TTS

## Platform Notes

| Platform | Whisper Engine | Notes |
|----------|---------------|-------|
| macOS (Apple Silicon) | mlx-whisper | Hardware-accelerated, fastest |
| macOS (Intel) | openai-whisper | CPU-based |
| Windows | openai-whisper | Requires Python 3.9+ |
| Linux | openai-whisper | GPU support via CUDA optional |

## License

MIT
