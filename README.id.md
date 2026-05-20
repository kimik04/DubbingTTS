# DubbingTTS

Bot dubbing video otomatis multi-bahasa. Mengubah video dari bahasa sumber (China, Inggris, Korea, Jepang, dll.) ke bahasa target (Indonesia, Inggris, dll.) menggunakan Gemini AI untuk transkripsi, identifikasi karakter, terjemahan, dan text-to-speech.

## Fitur

- Multi-project — setiap judul/drama adalah project independen
- Multi-bahasa — bahasa sumber dan target configurable per project
- Tiga mode transkripsi: subtitle (hardcoded), video (audio+visual), audio-only
- TTS per karakter via Gemini Live API dengan konsistensi suara
- Deteksi emosi — TTS bicara dengan emosi yang sesuai (happy, sad, angry)
- Duration-aware TTS — kecepatan bicara menyesuaikan timing dialog asli
- Karakter persisten lintas episode dengan auto-detection
- Full caching — bisa resume dari step manapun
- Cross-platform — Windows, macOS, Linux

## Instalasi

### Auto Setup (Rekomendasi)

Script setup otomatis detect OS dan install semua dependency:

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
python setup.py
```

Script akan install ffmpeg, yt-dlp, dan Python dependencies secara otomatis menggunakan package manager sistem (scoop/choco/winget di Windows, brew di macOS, apt/dnf/pacman di Linux).

### Manual Setup

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
pip install -r requirements.txt
cp config.yaml.example config.yaml
```

Install ffmpeg dan yt-dlp manual:

| OS | Command |
|----|---------|
| Windows | `scoop install ffmpeg yt-dlp` atau `choco install ffmpeg yt-dlp` |
| macOS | `brew install ffmpeg yt-dlp` |
| Linux | `sudo apt install ffmpeg` + `pip install yt-dlp` |

### Konfigurasi

Edit `config.yaml` dan masukkan Gemini API key:

```yaml
gemini_api_key: "gemini-api-key-kamu"
```

Dapatkan API key gratis di https://aistudio.google.com/apikey

Atau set via environment variable:

```bash
# macOS/Linux
export GEMINI_API_KEY="key-kamu"

# Windows PowerShell
$env:GEMINI_API_KEY="key-kamu"
```

## Cara Pakai

### 1. Buat Project (Otomatis)

Cukup paste URL episode 1 — bot otomatis detect judul dan scrape semua link episode:

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

Saat ini support auto-scraping untuk ReelShort. Untuk platform lain, pakai setup manual.

### 1b. Buat Project (Manual)

```bash
python -m src.cli init "Raja Judi Tanpa Mahkota" --source zh --target id
```

Lalu edit `projects/nama-project/links.txt`:

```
# Satu URL per baris, urutan = nomor episode
https://www.reelshort.com/id/episodes/episode-1-...
https://www.reelshort.com/id/episodes/episode-2-...
https://www.reelshort.com/id/episodes/episode-3-...
```

Support: ReelShort, YouTube, URL MP4 langsung, atau path file lokal.

### 2. Jalankan Dubbing

```bash
# Dub satu episode
python -m src.cli dub --project senyum-manis-di-bibirnya --episode 1

# Dub semua episode
python -m src.cli dub --project raja-judi-tanpa-mahkota

# Dub dari URL spesifik
python -m src.cli dub --project raja-judi-tanpa-mahkota --url "https://..."
```

Output video ada di `projects/nama-project/output/ep1_dubbed.mp4`.

### 4. Re-run atau Fix Step Tertentu

Kalau ada yang salah, bisa re-run step individual:

```bash
# Re-identify karakter (upload ulang video ke Gemini)
python -m src.cli identify --project slug --episode 1 --force

# Re-generate TTS semua karakter
python -m src.cli tts --project slug --episode 1 --force

# Re-generate TTS satu karakter saja
python -m src.cli tts --project slug --episode 1 --character "Yosa Leostra" --force

# Re-mix audio (kalau ubah audio settings)
python -m src.cli mix --project slug --episode 1 --force
```

### 5. Kelola Karakter

```bash
# List semua karakter di project
python -m src.cli characters --project slug

# Tambah karakter manual dengan voice tertentu
python -m src.cli characters --project slug --add "Kakek" --voice Gacrux --gender male
```

### 6. Preview (Tanpa TTS)

Preview hanya jalankan identify — berguna untuk cek transkripsi dan terjemahan sebelum generate TTS:

```bash
python -m src.cli preview --project slug --episode 1
```

### 7. List Project

```bash
python -m src.cli projects
```

## Pipeline

```
Video → Download → Demucs (pisahkan vokal) → Gemini (identify + translate) → TTS → Mix → Video Dubbed
```

| Step | Apa yang dilakukan | Output |
|------|-------------------|--------|
| Download | Ambil video via yt-dlp, ekstrak audio | `cache/ep1/video.mp4`, `audio.mp3` |
| Separate | Demucs pisahkan vokal dari background | `cache/ep1/vocals.wav`, `no_vocals.wav` |
| Identify | Gemini tonton video, identifikasi speaker, translate | `cache/ep1/identified_segments.json` |
| TTS | Gemini Live generate suara per karakter | `cache/ep1/tts/{Karakter}/seg_XXXX.wav` |
| Mix | ffmpeg tempatkan TTS di timestamp, mix dengan background | `output/ep1_dubbed.mp4` |

## Mode Transkripsi

Set di `config.yaml` bagian `transcription.source`:

| Mode | Deskripsi | Cocok untuk |
|------|-----------|-------------|
| `subtitle` | Baca subtitle hardcode dari frame video, timestamp = subtitle muncul | Video dengan subtitle burned-in (teks paling akurat) |
| `video` | Transkripsi dari audio + visual, timestamp = kapan dengar suara | Video tanpa subtitle |
| `audio` | Transkripsi dari suara saja (tidak perlu upload video) | Konten audio-only atau hemat quota API |

## Referensi Konfigurasi

```yaml
# config.yaml

# Gemini API key (atau set env var GEMINI_API_KEY)
gemini_api_key: "key-kamu"

# Model
models:
  transcribe: "gemini-3-flash-preview"       # Untuk analisis video/audio
  tts: "gemini-3.1-flash-live-preview"       # Untuk TTS via Live API

# Pengaturan audio
audio:
  sample_rate: 24000    # Sample rate output TTS (Hz)
  max_speed: 1.5        # Max atempo speedup untuk TTS yang melebihi durasi slot
  bg_volume: 1.0        # Volume background music di final mix
  dub_volume: 0.7       # Volume suara dubbing di final mix

# Mode transkripsi
transcription:
  source: "subtitle"    # "subtitle", "video", atau "audio"

# Pengaturan Demucs
demucs:
  model: "htdemucs"     # Model Demucs untuk pemisahan vokal
  two_stems: true       # Pisahkan jadi vocals + no_vocals saja
```

## Voice TTS yang Tersedia

Semua voice dari Gemini Live API. Gunakan nama persis ini di `characters.yaml`:

| Laki-laki | Perempuan |
|-----------|-----------|
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

## Struktur Project

```
DubbingTTS/
├── setup.py                 # Script auto-setup (detect OS, install deps)
├── config.yaml              # Config global (API key, model, audio)
├── config.yaml.example      # Template config
├── requirements.txt         # Python dependencies
├── projects/
│   └── {slug}/
│       ├── project.yaml     # Pengaturan bahasa (source/target)
│       ├── characters.yaml  # Database karakter (voice, gender, persisten)
│       ├── links.txt        # URL episode (satu per baris)
│       ├── cache/
│       │   └── ep{N}/
│       │       ├── video.mp4
│       │       ├── audio.mp3
│       │       ├── vocals.wav
│       │       ├── no_vocals.wav
│       │       ├── identified_segments.json
│       │       └── tts/{Karakter}/seg_XXXX.wav
│       └── output/
│           └── ep{N}_dubbed.mp4
└── src/
    ├── cli.py               # Entry point CLI
    ├── downloader.py        # Download video + demucs separation
    ├── character_id.py      # Gemini: transkripsi + identifikasi + translate
    ├── tts_engine.py        # Gemini Live API: text-to-speech
    ├── mixer.py             # ffmpeg: mix TTS + background + video
    └── utils.py             # Helper (config, segments, retry)
```

## Bahasa yang Didukung

**Sumber** (bahasa video asli):
zh, en, ko, ja, th, es, fr, de, ru, ar, dan bahasa apapun yang Gemini pahami

**Target** (bahasa dubbing):
id, en, zh, ms, th, dan bahasa apapun yang Gemini TTS bisa ucapkan

## Troubleshooting

| Masalah | Solusi |
|---------|--------|
| 429 Too Many Requests | Tunggu 1-2 menit, coba lagi. Free tier = 5 RPM |
| 1011 Internal Error di TTS | Nama voice invalid atau server overload. Cek daftar voice di atas |
| TTS terlalu cepat/lambat | Adjust `audio.max_speed` di config (1.0-2.0) |
| Dubbing tidak sinkron | Coba `transcription.source: "subtitle"` untuk video dengan subtitle hardcode |
| Karakter tidak konsisten antar episode | Cek `characters.yaml` — nama harus persis sama |
| Demucs gagal | Install `soundfile`: `pip install soundfile` |

## Lisensi

MIT
