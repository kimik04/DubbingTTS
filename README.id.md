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

## Kebutuhan Sistem

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/download.html) di PATH
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) di PATH
- [Gemini API key](https://aistudio.google.com/apikey)

## Instalasi

```bash
git clone https://github.com/kimik04/DubbingTTS.git
cd DubbingTTS
pip install -r requirements.txt
cp config.yaml.example config.yaml
# Edit config.yaml — masukkan Gemini API key
```

Atau set via environment variable:

```bash
export GEMINI_API_KEY="key-kamu"
```

## Cara Pakai

```bash
# Buat project baru
python -m src.cli init "Judul Drama" --source zh --target id

# Tambahkan URL video ke projects/judul-drama/links.txt

# Dub semua episode
python -m src.cli dub --project judul-drama

# Atau dub satu episode
python -m src.cli dub --project judul-drama --episode 1
```

## Pipeline

```
Video → Download → Demucs (pisahkan vokal) → Gemini (identify + translate) → TTS → Mix → Video Dubbed
```

| Step | Deskripsi | Tool |
|------|-----------|------|
| Download | Ambil video + ekstrak audio | yt-dlp, ffmpeg |
| Separate | Pisahkan vokal dari background | Demucs |
| Identify | Transkripsi + speaker ID + translate + emosi | Gemini API |
| TTS | Text-to-speech per karakter | Gemini Live WebSocket |
| Mix | Tempatkan TTS di timestamp + mux video | ffmpeg |

## Mode Transkripsi

Set di `config.yaml` bagian `transcription.source`:

| Mode | Deskripsi | Cocok untuk |
|------|-----------|-------------|
| `subtitle` | Baca subtitle hardcode dari frame video | Video dengan subtitle burned-in |
| `video` | Transkripsi dari audio + visual | Video tanpa subtitle |
| `audio` | Transkripsi dari suara saja | Konten audio-only |

## Perintah CLI

```bash
# Manajemen project
python -m src.cli init "Judul" --source zh --target id
python -m src.cli projects

# Full pipeline
python -m src.cli dub --project slug --episode 1
python -m src.cli dub --project slug                    # semua episode

# Step individual
python -m src.cli identify --project slug --episode 1
python -m src.cli tts --project slug --episode 1
python -m src.cli tts --project slug --episode 1 --character "Nama"
python -m src.cli mix --project slug --episode 1

# Manajemen karakter
python -m src.cli characters --project slug
python -m src.cli characters --project slug --add "Nama" --voice Puck --gender male

# Preview (identify saja, tanpa TTS)
python -m src.cli preview --project slug --episode 1
```

## Konfigurasi

```yaml
# config.yaml
gemini_api_key: "key-kamu"

models:
  transcribe: "gemini-3-flash-preview"
  tts: "gemini-3.1-flash-live-preview"

audio:
  sample_rate: 24000
  max_speed: 1.5
  bg_volume: 1.0
  dub_volume: 0.7

transcription:
  source: "subtitle"   # atau "video" atau "audio"
```

## Voice TTS yang Tersedia

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

Lihat `config.yaml.example` untuk daftar lengkap 28 voice.

## Struktur Project

```
DubbingTTS/
├── config.yaml              # Config global
├── projects/
│   └── {slug}/
│       ├── project.yaml     # Pengaturan bahasa
│       ├── characters.yaml  # Database karakter (persisten)
│       ├── links.txt        # URL episode
│       ├── cache/ep{N}/     # File intermediate
│       └── output/          # Video dubbed final
└── src/
    ├── cli.py               # Entry point CLI
    ├── downloader.py        # Download + demucs separation
    ├── character_id.py      # Gemini transkripsi + identifikasi
    ├── tts_engine.py        # Gemini Live TTS
    ├── mixer.py             # Mixing audio + muxing video
    └── utils.py             # Helper functions
```

## Lisensi

MIT
