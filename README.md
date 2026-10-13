# 🎙️ CleanTake — one file in, one file out

Local, open-source audio cleanup. Drop noisy recordings in → get clean audio out.
Replaces Descript Studio Sound / Krisp / Auphonic — for $0, with **nothing ever uploaded anywhere**.

## v2.0.0 — Free Forever

**Everything is free. No license key, no paywall, no checkout, no tiers.**
v2.0.0 merged every feature from the old paid "Pro" build into the free app
and deleted the license gate. What you download here is the whole thing.

## Features (all included)

- **Batch processing** — drop many files at once; they're cleaned one by one with a live progress page. One bad file never kills the batch.
- **Watch-folder** — point at a folder; new files are processed automatically while the app stays open, into `CleanTake-Output` inside it.
- **Silence trimming** — cuts silences longer than 0.7 s (adjustable), keeping 0.15 s of breathing room on each side so natural pauses aren't clipped.
- **Loudness presets (1-click)** — Podcast (−16 LUFS) / YouTube (−14 LUFS) / Audiobook (−19 LUFS).
- **3 cleanup presets** — Gentle / Balanced / Strong (see table below).
- **Video support** — feed it an mp4/mov/webm… and get back either an MP3 of the clean audio, or the **same video with clean audio remuxed in** (video stream untouched, no re-encode).
- **Before/after player** on the results page, per-file download, and one **"Download all (.zip)"** button.
- **100% local** — Flask server binds to `127.0.0.1` only. Your audio never leaves your machine.

## How it works

```
audio/video file
  → ffmpeg: convert to 48 kHz mono wav (what DeepFilterNet expects)
  → DeepFilterNet (local binary): remove noise & reverb
  → silence trim (optional): ffmpeg silencedetect
  → ffmpeg loudnorm: level to your chosen LUFS preset
  → <name>_clean.mp3   (or <name>_clean.mp4 for video mode)
```

## Presets

A/B-tested 2026-10-01 on a real voice recording with fan noise (25 s).
Noise floor measured as mean RMS over the same 6 silence gaps; speech energy over 8 speech windows.

| Preset | Flags | Noise floor | Δ vs original (−32.5 dB) | Speech energy |
|---|---|---|---|---|
| Gentle | `--atten-lim-db 40` | −55.9 dB | −23.3 dB | −15.7 dB |
| Balanced (default) | *(none — DeepFilterNet defaults)* | −58.9 dB | −26.4 dB | −15.7 dB |
| Strong | `--pf --pf-beta 0.05` | −64.8 dB | **−32.3 dB** | −15.7 dB |

Speech energy is **identical across all presets** — even Strong doesn't eat the voice.
Loudness is leveled to your chosen preset (default Podcast −16 LUFS).

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5057** in your browser. Pick a preset, drop files in, hit "Clean them".

Requirements: Python 3.10+, `ffmpeg` on PATH. The DeepFilterNet binary (`bin/deep_filter`, Linux x86_64) is included.

## Windows build

Push a tag like `v2.0.0` → GitHub Actions builds the Windows package automatically
(portable ZIP + `setup.exe`, built with PyInstaller). Grab it from the release page.

> **"Windows protected your PC" (SmartScreen)?** This is expected — the build is
> unsigned open-source software, not a virus. Three clicks:
> 1. Click **"More info"**
> 2. Click **"Run anyway"**
> 3. Done — the app opens in your browser at http://127.0.0.1:5057
>
> Still unsure? The full source is in this repo — build it yourself with
> `pip install pyinstaller && pyinstaller --onefile app.py`.

## Tests

```bash
venv/bin/python test_full.py
```

Verifies: batch of 3 files, silence trimming, all 3 loudness presets within ±1 LUFS,
denoise noise floor, voice-energy preservation.

## Credits

- [FFmpeg](https://ffmpeg.org) — LGPL v2.1
- [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) by Rikorose — MIT / Apache-2.0
- CleanTake itself — MIT (see [LICENSE](LICENSE))

## Honest limits

- **Best for steady background noise** (fan, hum, room tone). It **can't separate
  overlapping voices** — e.g. café chatter will stay chatter.
- Single-pass loudnorm in the free pipeline; the full pipeline uses dual-pass loudnorm for accuracy.
- Uploaded files stay in `uploads/`/`outputs/` — no auto-cleanup yet.
- UI is functional, not beautiful. Faceless by design.
