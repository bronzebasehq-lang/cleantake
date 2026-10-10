"""CleanTake Pro pipeline: free pipeline + silence trimming + loudness presets.

Order per file: any audio/video -> 48kHz wav -> DeepFilterNet denoise
-> silence trim (optional) -> loudnorm at the chosen LUFS preset -> mp3
(or clean audio remuxed back into the original video).

Loudness presets (single-pass loudnorm, same as the free build):
  podcast   : -16 LUFS  (podcast standard)
  youtube   : -14 LUFS  (YouTube normalization target)
  audiobook : -19 LUFS  (ACX-style audiobook target)
"""
import json
import os
import re
import shutil
import subprocess

from pipeline import (
    WORK_SR, VIDEO_EXTS, deep_filter_bin, ffmpeg_bin, _run,
    to_wav_48k, deepfilter_enhance, video_out,
)

# preset -> (integrated LUFS, true peak dBTP, LRA)
LOUDNESS_PRESETS = {
    "podcast":   (-16, -1.5, 11),
    "youtube":   (-14, -1.0, 11),
    "audiobook": (-19, -1.5, 11),
}


def loudnorm_preset(wav_in, out_mp3, preset="podcast"):
    """Level loudness to a named preset target (dual-pass for accuracy).

    Single-pass loudnorm can undershoot when the true-peak limiter engages;
    the second pass applies the measured values so output lands on target.
    """
    if preset not in LOUDNESS_PRESETS:
        raise ValueError(
            f"Unknown loudness preset {preset!r} "
            f"(choose: {', '.join(sorted(LOUDNESS_PRESETS))})")
    i, tp, lra = LOUDNESS_PRESETS[preset]
    # Pass 1: measure. NOTE: no "-v error" here — the JSON summary is printed
    # at info level and would be suppressed.
    p = _run([ffmpeg_bin(), "-hide_banner", "-i", wav_in,
              "-af", f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json",
              "-f", "null", "-"])
    dec = json.JSONDecoder()
    measured = None
    idx = p.stderr.find("{")
    while idx != -1 and measured is None:
        try:
            measured, _ = dec.raw_decode(p.stderr[idx:])
        except json.JSONDecodeError:
            idx = p.stderr.find("{", idx + 1)
    if not measured or "input_i" not in measured:
        raise RuntimeError("loudnorm measurement pass produced no JSON")
    _run([ffmpeg_bin(), "-y", "-v", "error", "-i", wav_in,
          "-af", "loudnorm=I={i}:TP={tp}:LRA={lra}"
                  ":measured_I={mi}:measured_TP={mtp}:measured_LRA={mlra}"
                  ":measured_thresh={mth}:offset={mo}:linear=true".format(
                      i=i, tp=tp, lra=lra,
                      mi=measured["input_i"], mtp=measured["input_tp"],
                      mlra=measured["input_lra"], mth=measured["input_thresh"],
                      mo=measured["target_offset"]),
          "-ar", str(WORK_SR), "-c:a", "libmp3lame", "-b:a", "192k", out_mp3])


def detect_silences(wav_path, min_silence=0.7, threshold_db=-40.0):
    """Return [(start, end), ...] of silences >= min_silence seconds.

    Uses ffmpeg silencedetect. Times are in seconds (float).
    """
    p = subprocess.run(
        [ffmpeg_bin(), "-v", "info", "-i", wav_path,
         "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True)
    starts, ends = [], []
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m:
            starts.append(float(m.group(1)))
        m = re.search(r"silence_end:\s*([0-9.]+).*?silence_duration:\s*([0-9.]+)",
                      line)
        if m:
            ends.append((float(m.group(1)), float(m.group(2))))
    # Pair each end with the most recent start before it.
    silences = []
    si = 0
    for end_t, dur in ends:
        while si < len(starts) and starts[si] <= end_t:
            s = starts[si]
            si += 1
            if end_t - s >= min_silence - 1e-6:
                silences.append((s, end_t))
    # Trailing silence that runs to EOF: silencedetect reports start but no end.
    if si < len(starts):
        dur = probe_duration(wav_path)
        for s in starts[si:]:
            if dur - s >= min_silence - 1e-6:
                silences.append((s, dur))
    return silences


def probe_duration(path):
    """Duration in seconds, parsed from `ffmpeg -i` (no ffprobe needed —
    the Windows bundle only ships ffmpeg.exe)."""
    p = subprocess.run(
        [ffmpeg_bin(), "-i", path],
        capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([0-9.]+)", p.stderr)
    if not m:
        raise RuntimeError("could not read duration of " + path)
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def trim_silence(wav_in, wav_out, min_silence=0.7, threshold_db=-40.0,
                 padding=0.15):
    """Cut silences longer than min_silence, keeping `padding` seconds of
    silence at each end of a cut so natural breathing isn't clipped.

    Returns (wav_out, seconds_removed).
    """
    silences = detect_silences(wav_in, min_silence, threshold_db)
    duration = probe_duration(wav_in)
    # Build keep-segments: everything except [s+padding, e-padding] per silence.
    cuts = []
    for s, e in silences:
        cs, ce = s + padding, e - padding
        if ce > cs + 0.05:  # ignore slivers
            cuts.append((cs, ce))
    if not cuts:
        shutil.copyfile(wav_in, wav_out)
        return wav_out, 0.0
    keep = []
    pos = 0.0
    for cs, ce in sorted(cuts):
        if cs > pos + 0.01:
            keep.append((pos, cs))
        pos = max(pos, ce)
    if pos < duration - 0.01:
        keep.append((pos, duration))
    if not keep:
        raise RuntimeError("trim_silence would remove the entire file")
    select = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keep)
    _run([ffmpeg_bin(), "-y", "-v", "error", "-i", wav_in,
          "-af", f"aselect='{select}',asetpts=N/SR/TB",
          "-ar", str(WORK_SR), "-c:a", "pcm_s16le", wav_out])
    removed = sum(ce - cs for cs, ce in cuts)
    return wav_out, removed


def process_file_pro(src_path, workdir, preset="balanced", video_mode="mp3",
                     loudness="podcast", trim=True, min_silence=0.7):
    """Full Pro pipeline. Returns dict of artifact paths + stats.

    preset: DeepFilterNet strength "gentle" | "balanced" | "strong".
    loudness: "podcast" | "youtube" | "audiobook".
    trim: cut long silences (after denoise, before loudnorm).
    """
    base = os.path.splitext(os.path.basename(src_path))[0]
    ext = os.path.splitext(src_path)[1].lower()
    is_video = ext in VIDEO_EXTS
    wav48 = os.path.join(workdir, f"{base}_48k.wav")
    enhanced = os.path.join(workdir, f"{base}_enhanced.wav")
    to_wav_48k(src_path, wav48)
    deepfilter_enhance(wav48, enhanced, preset=preset)

    trimmed = enhanced
    removed = 0.0
    if trim:
        trimmed = os.path.join(workdir, f"{base}_trimmed.wav")
        _, removed = trim_silence(enhanced, trimmed, min_silence=min_silence)

    if is_video and video_mode == "video":
        norm_mp3 = os.path.join(workdir, f"{base}_norm.mp3")
        loudnorm_preset(trimmed, norm_mp3, preset=loudness)
        final = video_out(workdir, src_path, norm_mp3, base)
    else:
        final = os.path.join(workdir, f"{base}_clean.mp3")
        loudnorm_preset(trimmed, final, preset=loudness)
    return {"converted": wav48, "enhanced": enhanced,
            "trimmed": trimmed, "final": final,
            "is_video": is_video, "preset": preset, "loudness": loudness,
            "silence_removed_s": round(removed, 2)}
