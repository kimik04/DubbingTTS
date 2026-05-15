# DubbingTTS — Automated Video Dubbing Bot

## Overview

Bot CLI untuk dubbing otomatis video multi-bahasa. Support berbagai bahasa sumber (Chinese, English, Korean, Japanese, dll) ke bahasa target (Indonesian, English, dll). Bisa handle banyak judul/project sekaligus dengan karakter terpisah per judul.

## Architecture

```
DubbingTTS/
├── config.yaml              # Konfigurasi global (API keys, defaults)
├── projects/                # Setiap judul/drama = 1 project
│   ├── pengantin-kontrak/
│   │   ├── project.yaml     # Config per project (bahasa, karakter, scenes)
│   │   ├── characters.yaml  # Database karakter project ini
│   │   ├── links.txt        # URL episodes project ini
│   │   ├── cache/           # Intermediate files
│   │   └── output/          # Final dubbed videos
│   ├── action-movie-en/
│   │   ├── project.yaml
│   │   ├── characters.yaml
│   │   ├── links.txt
│   │   ├── cache/
│   │   └── output/
│   └── ...
├── src/
│   ├── __init__.py
│   ├── cli.py               # Entry point CLI (argparse)
│   ├── downloader.py        # Download video dari URL (yt-dlp)
│   ├── transcriber.py       # MLX Whisper transcription + timestamps
│   ├── character_id.py      # Gemini character identification
│   ├── tts_engine.py        # Gemini 3.1 Flash Live TTS (per karakter)
│   ├── mixer.py             # Audio mixing + video muxing
│   └── utils.py             # Helpers (timestamp parsing, file ops)
└── requirements.txt
```

## Config (`config.yaml`) — Global

```yaml
# API Keys
gemini_api_key: "AIzaSy..."

# Models
models:
  whisper: "mlx-community/whisper-small-mlx"
  transcribe: "gemini-2.5-flash-lite"       # Character ID + translation
  tts: "gemini-3.1-flash-live-preview"      # TTS via WebSocket

# Audio settings (defaults, bisa override per project)
audio:
  sample_rate: 24000
  max_speed: 2.0
  bg_volume: 1.0
  dub_volume: 0.7

# Demucs
demucs:
  model: "htdemucs"
  two_stems: true

# Supported languages
languages:
  source: ["zh", "en", "ko", "ja", "th", "es", "fr", "de", "ru", "ar"]
  target: ["id", "en", "zh", "ms", "th"]
```

## Project Config (`projects/{name}/project.yaml`)

Setiap judul/drama punya config sendiri:

```yaml
# Project metadata
title: "Pengantin Kontrak, Tapi Terlalu Liar"
slug: "pengantin-kontrak"

# Language settings
language:
  source: "zh"          # Bahasa sumber (Whisper language code)
  target: "id"          # Bahasa target (untuk translation + TTS)

# Override global audio settings (optional)
audio:
  bg_volume: 0.8
  dub_volume: 0.75

# Scene context per episode (untuk bantu identifikasi karakter)
episodes:
  ep1:
    scenes:
      - time: "0:00-1:20"
        description: "Villain captured Adrian. Chloe rescues him."
      - time: "1:20-1:40"
        description: "Adrian begs Chloe to stay. Chloe negotiates."
      - time: "2:00-2:20"
        description: "Asisten reports to Adrian about the woman."
      - time: "2:30-3:30"
        description: "Market scene. Pedagang gossip. Chloe sells meat."
```

Contoh project English → Indonesian:

```yaml
title: "John Wick"
slug: "john-wick"

language:
  source: "en"
  target: "id"

episodes:
  ep1:
    scenes:
      - time: "0:00-5:00"
        description: "John mourns his wife. Receives puppy."
```

## Character Database (`characters.yaml`)

Persisten lintas episode. Karakter baru otomatis ditambahkan saat terdeteksi.

```yaml
# Format: nama karakter → voice config
characters:
  Adrian:
    voice: "Puck"
    gender: "male"
    description: "Male lead, young CEO, calm and commanding voice"
    aliases: ["谢少", "席总", "谢家大少", "四少"]
    first_seen: "ep1"

  Chloe:
    voice: "Aoede"
    gender: "female"
    description: "Female lead, butcher girl, energetic/brave/loud voice"
    aliases: ["陆小秋"]
    first_seen: "ep1"

  Asisten:
    voice: "Fenrir"
    gender: "male"
    description: "Adrian's male assistant/secretary, polite and formal"
    aliases: []
    first_seen: "ep1"

  Villain:
    voice: "Zephyr"
    gender: "female"
    description: "Female antagonist, scheming and cruel, older female voice"
    aliases: ["诛婆龙", "女反派"]
    first_seen: "ep1"

  Pedagang:
    voice: "Charon"
    gender: "male"
    description: "Market vendors, casual male voices"
    aliases: []
    first_seen: "ep1"

  Ibu:
    voice: "Kore"
    gender: "female"
    description: "Mother/older woman, warm maternal tone"
    aliases: []
    first_seen: "ep1"

# Scene context per episode (untuk bantu identifikasi)
episodes:
  ep1:
    scenes:
      - time: "0:00-1:20"
        description: "Villain captured Adrian. Chloe rescues him."
      - time: "1:20-1:40"
        description: "Adrian begs Chloe to stay. Chloe negotiates."
      - time: "2:00-2:20"
        description: "Asisten reports to Adrian about the woman."
      - time: "2:30-3:30"
        description: "Market scene. Pedagang gossip. Chloe sells meat."
```

## Pipeline Flow

### Step 1: Download
```
Input: links.txt (atau --url flag)
Output: cache/ep{N}/video.mp4, cache/ep{N}/audio.mp3
```
- Pakai yt-dlp
- Auto-detect episode number dari filename/order
- Skip jika sudah ada di cache

### Step 2: Transcribe (MLX Whisper)
```
Input: cache/ep{N}/audio.mp3
Output: cache/ep{N}/whisper_segments.json
```
- Word-level timestamps
- Language: zh (Chinese)
- Output: [{start, end, text}, ...]

### Step 3: Character Identification (Gemini)
```
Input: audio.mp3 + whisper_segments.json + characters.yaml
Output: cache/ep{N}/identified_segments.json
```
- Upload audio ke Gemini
- Kirim segments + character database + scene context
- Gemini identify siapa yang ngomong berdasarkan:
  - Voice/gender dari audio
  - Content/context dari dialog
  - Scene breakdown
- Output: [{start, end, text, character, translation}, ...]
- **Jika ada karakter baru**: otomatis tambah ke characters.yaml dengan voice assignment

### Step 4: TTS Per Karakter (Gemini 3.1 Flash Live)
```
Input: identified_segments.json
Output: cache/ep{N}/tts/{character}/seg_{N}.wav
```

**PENTING: Proses per karakter, bukan per segment sequential.**

```
Untuk setiap karakter:
  1. Kumpulkan semua segment milik karakter tersebut
  2. Buka 1 WebSocket session dengan voice karakter tersebut
  3. Generate TTS untuk semua segment karakter itu
  4. Simpan per file: tts/Adrian/seg_03.wav, tts/Adrian/seg_22.wav, dll
  5. Atempo adjustment jika TTS > slot duration
```

Keuntungan per-karakter:
- Voice consistency (1 session = 1 voice)
- Lebih efisien (reuse WebSocket connection)
- Mudah re-generate 1 karakter tanpa ulang semua

### Step 5: Mix & Merge
```
Input: semua TTS wav + demucs no_vocals.wav + video.mp4
Output: output/ep{N}_dubbed.mp4
```
1. Demucs: pisahkan vocals dari background
2. Buat silence base (durasi = video)
3. Place setiap TTS wav di timestamp yang benar (adelay)
4. Amix semua → dub_raw.wav
5. Volume boost + limiter → dub_loud.wav
6. Mix dub_loud + no_vocals background → final.mp3
7. Mux final.mp3 + video → output.mp4

## CLI Usage

```bash
# === Project Management ===
# Buat project baru
python -m src.cli init "Pengantin Kontrak" --source zh --target id

# List semua projects
python -m src.cli projects

# === Dubbing ===
# Dub semua episode di project
python -m src.cli dub --project pengantin-kontrak

# Dub URL spesifik (auto-detect atau specify project)
python -m src.cli dub --project pengantin-kontrak --url "https://..."

# Dub dari file link custom
python -m src.cli dub --project pengantin-kontrak --links mylinks.txt

# Dub episode tertentu saja
python -m src.cli dub --project pengantin-kontrak --episode 3

# === Per-step ===
python -m src.cli transcribe --project pengantin-kontrak --episode 1
python -m src.cli identify --project pengantin-kontrak --episode 1
python -m src.cli tts --project pengantin-kontrak --episode 1
python -m src.cli tts --project pengantin-kontrak --episode 1 --character Adrian
python -m src.cli mix --project pengantin-kontrak --episode 1

# === Character Management ===
python -m src.cli characters --project pengantin-kontrak
python -m src.cli characters --project pengantin-kontrak --add "Kakek" --voice Orus --gender male

# === Preview ===
python -m src.cli preview --project pengantin-kontrak --episode 1
```

## Links File (`links.txt`)

```
# Episode 1
https://www.example.com/video1.mp4

# Episode 2
https://www.example.com/video2.mp4

# Bisa juga local path
/path/to/local/video.mp4
```

- Satu URL per baris
- Baris kosong dan `#` comment di-skip
- Urutan = episode number (ep1, ep2, ...)
- Support: YouTube, direct MP4, local file

## Karakter Sinkronisasi Lintas Episode

```
Episode 1: Adrian, Chloe, Villain, Asisten, Pedagang
Episode 2: Adrian, Chloe, Villain, Asisten, + NEW: Kakek (auto-detected)
Episode 3: semua karakter ep1+ep2 + NEW: Polisi
```

Saat identify karakter di ep2+:
1. Load characters.yaml (semua karakter yang sudah dikenal)
2. Kirim ke Gemini sebagai context
3. Jika Gemini detect suara baru yang tidak cocok karakter existing → flag sebagai "Unknown_N"
4. Prompt user (atau auto-assign) voice untuk karakter baru
5. Update characters.yaml

## Error Handling

- **Rate limit (429)**: exponential backoff, max 3 retry per segment
- **WebSocket disconnect**: reconnect + retry segment yang gagal
- **Whisper timeout**: fallback ke whisper-small jika large gagal
- **Partial failure**: simpan progress, bisa resume dari step terakhir
- **Cache**: semua intermediate di-cache, re-run skip yang sudah selesai

## Dependencies

```
mlx-whisper
websockets
requests
pyyaml
yt-dlp
ffmpeg (system)
demucs
```

## Gemini Voice Options

Available voices untuk assignment karakter baru:
- **Male**: Puck, Charon, Fenrir, Orus, Leda
- **Female**: Aoede, Kore, Zephyr, Elara, Vesta

## Multi-Language Support

### Whisper Language Detection
- Whisper auto-detect bahasa jika `source` tidak di-set
- Atau explicit set di `project.yaml`: `language.source: "zh"`
- Supported: zh, en, ko, ja, th, es, fr, de, ru, ar, dan 90+ bahasa lainnya

### Translation Target
- Gemini translate ke bahasa target yang di-set di `project.yaml`
- Prompt Gemini di-adjust otomatis berdasarkan `language.target`
- TTS output dalam bahasa target

### TTS Language Handling
- Gemini Live TTS support multi-bahasa natively
- System instruction di-adjust: "Dub into {target_language}"
- Voice selection tetap dari characters.yaml (voice Gemini universal)

### Contoh Kombinasi
| Source | Target | Use Case |
|--------|--------|----------|
| zh → id | Drama China ke Indonesia |
| en → id | Film/series English ke Indonesia |
| ko → id | K-Drama ke Indonesia |
| ja → id | Anime ke Indonesia |
| en → zh | English content ke Chinese |
| id → en | Konten Indonesia ke English |

## Notes

- Semua config di `config.yaml` + `project.yaml`, TIDAK hardcode di source
- API key bisa juga via environment variable `GEMINI_API_KEY`
- Output naming: `projects/{slug}/output/ep{N}_dubbed.mp4`
- Cache structure memungkinkan partial re-run
- TTS per karakter = voice lebih konsisten + debugging lebih mudah
- Setiap project independen — karakter, scenes, bahasa terpisah
- `--project` flag wajib di semua command (kecuali `projects` dan `init`)
