# CleanTake Pro — dev notes (internal, not shipped)

## What it is
Paid companion to the free CleanTake app ($29 one-time, via Lemon Squeezy).
Same local-only Flask web UI pattern; four Pro features:

1. **Batch queue** — drop many audio/video files, processed sequentially with
   per-file progress + a summary at the end.
2. **Watch-folder** — set a folder; new files are auto-processed into
   `CleanTake-Pro-Output/` while the app stays open.
3. **Silence trimming** — ffmpeg silencedetect cuts silences longer than the
   threshold (default 0.7s), keeping 0.15s breathing room on each side.
   Runs after denoise, before loudnorm.
4. **Loudness presets** — 1-click: Podcast (-16 LUFS), YouTube (-14 LUFS),
   Audiobook (-19 LUFS). Single-pass loudnorm, same as the free build.

## Files
- `cleantake_pro.py` — Pro Flask app (entry point; PyInstaller builds this).
  Runs on port **5058** (free app uses 5057) so both can run side by side.
- `pipeline_pro.py` — `process_file_pro()` = free pipeline + trim + presets.
- `license.py` — `CT-PRO-XXXX-XXXX` format + check-digit validation, keygen.
- `.github/workflows/build-windows-pro.yml` — CI: tag `pro-v*` →
  windows-latest → PyInstaller onedir `CleanTake-Pro` → portable ZIP uploaded
  as a workflow artifact (NOT a public release).

## License keys
- Format `CT-PRO-XXXX-XXXX`, last char is a base36 check digit. Minimal gate,
  not DRM — real keys are issued by Lemon Squeezy after purchase.
- The dev key below is for testing only. NEVER bundle `license.key` into a
  shipped build (CI asserts this). The app shows a /license page on first run
  where the buyer pastes their key.

## Build & test
- Windows build: `git tag pro-v1.0.0 && git push origin pro-v1.0.0`
  (CI builds; download the artifact from the Actions run).
- Linux smoke test: `venv/bin/python -m pytest` — n/a; run
  `venv/bin/python test_pro.py` (pipeline + license checks on real audio).

## Versioning
Pro versions are independent from the free app: v1.0.0 = first Pro build.
