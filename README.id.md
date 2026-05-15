# DubbingTTS

Bot dubbing video otomatis multi-bahasa. Mengubah video dari bahasa sumber apapun (China, Inggris, Korea, Jepang, dll.) ke bahasa target (Indonesia, Inggris, dll.) menggunakan AI untuk transkripsi, identifikasi karakter, dan text-to-speech.

## Fitur

- Multi-project — setiap judul/drama adalah project independen dengan karakter dan pengaturan sendiri
- Multi-bahasa — bahasa sumber dan target bisa dikonfigurasi per project
- TTS per karakter — menjaga konsistensi suara dengan memproses semua segment per karakter dalam satu sesi
- Karakter persisten — database karakter berlaku lintas episode, karakter baru otomatis terdeteksi
- Full caching — semua hasil intermediate di-cache, pipeline bisa resume dari step manapun
- Cross-platform — berjalan di macOS (optimasi Apple Silicon), Windows, dan Linux

## Kebutuhan Sistem

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/download.html) terinstall dan ada di PATH
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) terinstall dan ada di PATH (untuk download URL)
- [Gemini API key](https://aistudio.google.com/apikey)

## Instalasi

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

# Install ffmpeg dan yt-dlp (pilih salah satu)
scoop install ffmpeg yt-dlp
# atau: choco install ffmpeg yt-dlp

pip install -r requirements.txt
```

### Linux

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
sudo apt install ffmpeg  # atau package manager distro kamu
pip install -r requirements.txt
```

### Konfigurasi

```bash
cp config.yaml.example config.yaml
# Edit config.yaml dan masukkan Gemini API key kamu
```

Atau set via environment variable:

```bash
# macOS/Linux
export GEMINI_API_KEY="key-kamu-disini"

# Windows PowerShell
$env:GEMINI_API_KEY="key-kamu-disini"
```

## Cara Pakai

```bash
# Buat project baru
python -m src.cli init "Judul Drama" --source zh --target id

# Tambahkan URL video ke projects/judul-drama/links.txt

# Jalankan full pipeline dubbing
python -m src.cli dub --project judul-drama --episode 1
```

## Pipeline

```
Video → Download → Transkripsi → Identifikasi Karakter → TTS → Mix → Video Dubbed
```

| Step | Deskripsi | Tool |
|------|-----------|------|
| 1. Download | Ambil video + ekstrak audio | yt-dlp, ffmpeg |
| 2. Transkripsi | Speech-to-text dengan timestamp | Whisper (MLX/OpenAI) |
| 3. Identifikasi | Identifikasi karakter + terjemahan | Gemini API |
| 4. TTS | Text-to-speech per karakter | Gemini Live WebSocket |
| 5. Mix | Pisahkan background + gabung | Demucs, ffmpeg |

## Perintah CLI

```bash
# Manajemen project
python -m src.cli init "Judul" --source zh --target id
python -m src.cli projects

# Full pipeline
python -m src.cli dub --project slug --episode 1
python -m src.cli dub --project slug                    # semua episode

# Step individual
python -m src.cli transcribe --project slug --episode 1
python -m src.cli identify --project slug --episode 1
python -m src.cli tts --project slug --episode 1
python -m src.cli tts --project slug --episode 1 --character Adrian
python -m src.cli mix --project slug --episode 1

# Manajemen karakter
python -m src.cli characters --project slug
python -m src.cli characters --project slug --add "Nama" --voice Puck --gender male

# Preview (transkripsi + identifikasi saja, tanpa TTS)
python -m src.cli preview --project slug --episode 1
```

## Struktur Project

```
DubbingTTS/
├── config.yaml              # Config global (API keys, model, audio settings)
├── projects/
│   └── {slug}/
│       ├── project.yaml     # Config per project (bahasa, scene)
│       ├── characters.yaml  # Database karakter (persisten lintas episode)
│       ├── links.txt        # URL video episode (satu per baris)
│       ├── cache/           # File intermediate (auto-generated)
│       └── output/          # Video dubbed final
└── src/
    ├── cli.py               # Entry point CLI
    ├── downloader.py        # Download video + ekstrak audio
    ├── transcriber.py       # Transkripsi Whisper
    ├── character_id.py      # Identifikasi karakter + terjemahan Gemini
    ├── tts_engine.py        # Gemini Live TTS (per karakter)
    ├── mixer.py             # Mixing audio + muxing video
    └── utils.py             # Helper functions
```

## Konfigurasi

### Global (`config.yaml`)

```yaml
gemini_api_key: "key-kamu"

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
title: "Drama Saya"
slug: "drama-saya"

language:
  source: "zh"    # Kode bahasa Whisper
  target: "id"    # Target terjemahan + TTS

episodes:
  ep1:
    scenes:
      - time: "0:00-1:30"
        description: "Deskripsi scene untuk konteks identifikasi karakter"
```

## Voice yang Tersedia

| Laki-laki | Perempuan |
|-----------|-----------|
| Puck      | Aoede     |
| Charon    | Kore      |
| Fenrir    | Zephyr    |
| Orus      | Elara     |
| Leda      | Vesta     |

## Bahasa yang Didukung

**Sumber** (transkripsi): zh, en, ko, ja, th, es, fr, de, ru, ar, dan 90+ lainnya

**Target** (dubbing): id, en, zh, ms, th, dan bahasa apapun yang didukung Gemini TTS

## Catatan Platform

| Platform | Whisper Engine | Catatan |
|----------|---------------|---------|
| macOS (Apple Silicon) | mlx-whisper | Hardware-accelerated, paling cepat |
| macOS (Intel) | faster-whisper | CTranslate2, ringan |
| Windows | faster-whisper | Tanpa PyTorch, ~4x lebih cepat dari openai-whisper |
| Linux | faster-whisper | CPU int8 default, CUDA opsional |

## Lisensi

MIT
