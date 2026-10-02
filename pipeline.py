"""CleanTake processing pipeline: any audio/video -> 48kHz wav -> DeepFilterNet
noise/echo removal (local binary) -> ffmpeg loudnorm leveling -> final mp3
(or clean-audio remuxed back into the original video).
100% local, no uploads anywhere.
"""
import os
import shutil
import subprocess
import sys

WORK_SR = 48000
BASE = os.path.dirname(os.path.abspath(__file__))

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

# Denoise presets. Flags verified against `deep_filter --help` (DeepFilterNet).
#  --atten-lim-db: 0 = no reduction, 100 = full (default 100)
#  --pf / --pf-beta: post-filter; higher beta = stronger attenuation (default 0.02).
# A/B-tested 2026-10-01 on a real voice+fan-noise recording (see README):
#  strong (--pf --pf-beta 0.05) removed the most noise (-64.8 dB floor vs -32.5
#  original) with zero measured speech-energy loss vs balanced.
PRESETS = {
    "gentle":   ["--atten-lim-db", "40"],
    "balanced": [],
    "strong":   ["--pf", "--pf-beta", "0.05"],
}


def resolve_binary(*names):
    """Find a tool binary: PyInstaller bundle dir first, then PATH.

    Works in dev (repo layout), in a PyInstaller one-dir build (exe next to
    files), and in a onefile build (sys._MEIPASS). Raises RuntimeError with a
    plain-English message when the tool can't be found.
    """
    bundle = getattr(sys, "_MEIPASS", BASE)
    tried = []
    for n in names:
        p = os.path.join(bundle, n)
        tried.append(p)
        if os.path.isfile(p):
            return p
    for n in names:
        hit = shutil.which(os.path.basename(n))
        if hit:
            return hit
        tried.append("PATH:" + os.path.basename(n))
    raise RuntimeError(
        "Couldn't find a required helper program "
        f"(looked for {', '.join(names)} in the app folder and on PATH). "
        "If you use the portable build, keep all files together in one folder."
    )


def deep_filter_bin():
    if sys.platform == "win32":
        return resolve_binary("deep_filter.exe", "deep-filter.exe")
    return resolve_binary(os.path.join("bin", "deep_filter"), "deep_filter")


def ffmpeg_bin():
    if sys.platform == "win32":
        return resolve_binary("ffmpeg.exe")
    return resolve_binary("ffmpeg")


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stderr[-2000:]}")
    return p


def to_wav_48k(src, dst):
    """Convert any audio/video input to 48kHz mono 16-bit wav (what DF expects)."""
    _run([ffmpeg_bin(), "-y", "-v", "error", "-i", src,
          "-ar", str(WORK_SR), "-ac", "1", "-c:a", "pcm_s16le", dst])


def deepfilter_enhance(wav_in, wav_out, preset="balanced"):
    """Remove noise/reverb with DeepFilterNet (local binary).

    preset: "gentle" (light), "balanced" (default), "strong" (aggressive).
    """
    if preset not in PRESETS:
        raise ValueError(
            f"Unknown preset {preset!r} (choose: {', '.join(sorted(PRESETS))})")
    outdir = os.path.dirname(os.path.abspath(wav_out))
    os.makedirs(outdir, exist_ok=True)
    _run([deep_filter_bin(), os.path.abspath(wav_in),
          "-o", outdir] + PRESETS[preset])
    produced = os.path.join(outdir, os.path.basename(wav_in))
    if not os.path.exists(produced):
        # fall back: find the newest wav in outdir
        cands = [os.path.join(outdir, f) for f in os.listdir(outdir)
                 if f.lower().endswith(".wav")]
        if not cands:
            raise RuntimeError("deep_filter produced no output file")
        produced = max(cands, key=os.path.getmtime)
    if os.path.abspath(produced) != os.path.abspath(wav_out):
        os.replace(produced, wav_out)


def loudnorm(wav_in, out_mp3):
    """Level loudness to podcast-ish target, single pass (MVP)."""
    _run([ffmpeg_bin(), "-y", "-v", "error", "-i", wav_in,
          "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
          "-ar", str(WORK_SR), "-c:a", "libmp3lame", "-b:a", "192k", out_mp3])


def video_out(workdir, original_video, clean_audio, base):
    """Remux clean audio back into the original video -> <base>_clean.mp4.

    Video stream is copied untouched (no re-encode); audio becomes 192k AAC.
    clean_audio may be wav or mp3.
    """
    out = os.path.join(workdir, f"{base}_clean.mp4")
    _run([ffmpeg_bin(), "-y", "-v", "error",
          "-i", original_video, "-i", clean_audio,
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
          "-shortest", "-movflags", "+faststart", out])
    return out


def process_file(src_path, workdir, preset="balanced", video_mode="mp3"):
    """Full pipeline. Returns dict of artifact paths.

    preset: "gentle" | "balanced" | "strong".
    video_mode: "mp3" (audio-only output) or "video" (clean audio remuxed
        back into the original video); only applies when src is a video file.
    """
    base = os.path.splitext(os.path.basename(src_path))[0]
    ext = os.path.splitext(src_path)[1].lower()
    is_video = ext in VIDEO_EXTS
    wav48 = os.path.join(workdir, f"{base}_48k.wav")
    enhanced = os.path.join(workdir, f"{base}_enhanced.wav")
    to_wav_48k(src_path, wav48)
    deepfilter_enhance(wav48, enhanced, preset=preset)
    if is_video and video_mode == "video":
        norm_mp3 = os.path.join(workdir, f"{base}_norm.mp3")
        loudnorm(enhanced, norm_mp3)
        final = video_out(workdir, src_path, norm_mp3, base)
    else:
        final = os.path.join(workdir, f"{base}_clean.mp3")
        loudnorm(enhanced, final)
    return {"converted": wav48, "enhanced": enhanced, "final": final,
            "is_video": is_video, "preset": preset}
