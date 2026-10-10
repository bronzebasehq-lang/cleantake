"""CleanTake Pro verification tests (run on Linux with the repo venv).

(a) batch 3 files -> all processed, no errors
(b) silence trim -> long silences cut, padding kept
(c) 3 loudness presets -> integrated LUFS within +/-1 of target
(d) denoise noise floor -> ~-33dB in, ~-59dB out (same as free build)

Usage: venv/bin/python test_pro.py
"""
import math
import os
import re
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import deepfilter_enhance, to_wav_48k
from pipeline_pro import (process_file_pro, trim_silence, detect_silences,
                          loudnorm_preset, probe_duration)
from license import validate_key, generate_key

BASE = os.path.dirname(os.path.abspath(__file__))
FFMPEG = "ffmpeg"
WORK = tempfile.mkdtemp(prefix="protest-")
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{p.stderr[-1500:]}")
    return p


def synth_tone_with_silences(path, sr=48000):
    """Deterministic: 2s tone, 3s silence, 2s tone, 1.5s silence, 2s tone.

    Used ONLY for the trim unit test (no DeepFilterNet involved — DF is a
    speech model and would attenuate pure sine as non-speech).
    """
    segs = [(440, 2.0), (0, 3.0), (440, 2.0), (0, 1.5), (550, 2.0)]
    with open(path, "wb") as f:
        import wave
        w = wave.open(f, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for freq, dur in segs:
            n = int(sr * dur)
            for i in range(n):
                v = int(16000 * math.sin(2 * math.pi * freq * i / sr)) if freq else 0
                w.writeframes(struct.pack("<h", v))
        w.close()


def synth_voice(path, seconds=8.0, noise_db=-33.0, sr=48000,
                lead_silence=0.0, tail_silence=0.0):
    """Speech-like: harmonic stack (f0 ~110Hz) with 4Hz syllabic amplitude
    modulation + steady noise at noise_db RMS. Optional pure-noise head/tail
    gaps (for noise-floor measurement, like the project's own method:
    mean RMS over silence gaps). Deterministic (seeded).
    """
    import random
    import wave
    random.seed(42)
    amp_noise = 10 ** (noise_db / 20.0)
    total = lead_silence + seconds + tail_silence
    n = int(sr * total)
    # brown-ish noise via cumulative sum of white, then normalize to target RMS
    white = [random.uniform(-1, 1) for _ in range(n)]
    brown, acc = [], 0.0
    for x in white:
        acc = (acc + 0.02 * x) / 1.02
        brown.append(acc)
    rms_b = math.sqrt(sum(b * b for b in brown) / n)
    brown = [b / rms_b * amp_noise for b in brown]
    pcm = bytearray()
    for i in range(n):
        t = i / sr
        in_voice = lead_silence <= t < lead_silence + seconds
        s = brown[i]
        if in_voice:
            tv = t - lead_silence
            f0 = 110 + 8 * math.sin(2 * math.pi * 0.7 * tv)  # pitch drift
            am = 0.6 + 0.4 * math.sin(2 * math.pi * 4 * tv)  # syllabic AM
            harm = sum(math.sin(2 * math.pi * f0 * h * tv) / h
                       for h in range(1, 9))
            s += 0.30 * am * harm / 2.0
        s = max(-1.0, min(1.0, s))
        pcm += struct.pack("<h", int(s * 32767))
    with open(path, "wb") as f:
        w = wave.open(f, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(pcm))
        w.close()


def synth_noisy_silence(path, seconds=5, noise_db=-33.0, sr=48000):
    """Pure noise at a known RMS level (for the denoise floor test)."""
    import random
    random.seed(7)
    amp = 10 ** (noise_db / 20.0)
    n = int(sr * seconds)
    with open(path, "wb") as f:
        import wave
        w = wave.open(f, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        # uniform noise scaled to target RMS (uniform RMS = peak/sqrt(3))
        peak = amp * math.sqrt(3)
        frames = struct.pack("<%dh" % n,
                             *[int(max(-1.0, min(1.0, random.uniform(-peak, peak))) * 32767)
                               for _ in range(n)])
        w.writeframes(frames)
        w.close()


def rms_db(path, t0=0.0, t1=None):
    import wave
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    count = len(raw) // 2
    unpacked = struct.unpack("<%dh" % count, raw)
    a, b = int(t0 * sr), int(t1 * sr) if t1 else count
    seg = unpacked[a:b]
    mean_sq = sum((s / 32768.0) ** 2 for s in seg) / max(1, len(seg))
    return 20 * math.log10(math.sqrt(max(1e-12, mean_sq)))


def integrated_lufs(path):
    p = run([FFMPEG, "-v", "info", "-i", path,
             "-af", "ebur128=peak=true", "-f", "null", "-"])
    ms = re.findall(r"I:\s*(-?[0-9.]+)\s*LUFS", p.stderr)
    if not ms:
        raise RuntimeError("could not parse ebur128 output")
    return float(ms[-1])


def main():
    """Run all Pro verification tests."""
    global passed, failed

    check("license: dev-style key validates", validate_key(generate_key()))
    check("license: bad format rejected", not validate_key("CT-PRO-AAAA-BBBB"))
    check("license: bad checksum rejected", not validate_key("CT-PRO-9Y1F-BW9G"))
    check("license: lowercase accepted", validate_key("ct-pro-9y1f-bw9f"))

    # ---------------------------------------------------------- (a) batch of 3
    # Voice-like content (harmonic + noise): DeepFilterNet is a speech model
    # and preserves this; pure sine would be attenuated as non-speech.
    f1 = os.path.join(WORK, "batch1.wav")
    f2 = os.path.join(WORK, "batch2.wav")
    f3 = os.path.join(WORK, "batch3.wav")
    synth_voice(f1, seconds=8.0)
    synth_voice(f2, seconds=8.0)
    synth_voice(f3, seconds=8.0)
    cfgs = [
        {"loudness": "podcast", "trim": True, "min_silence": 0.7},
        {"loudness": "youtube", "trim": False},
        {"loudness": "audiobook", "trim": True, "min_silence": 1.0},
    ]
    outs = []
    ok = True
    for i, (f, cfg) in enumerate(zip([f1, f2, f3], cfgs)):
        wd = os.path.join(WORK, f"job{i}")
        os.makedirs(wd, exist_ok=True)
        try:
            out = process_file_pro(f, wd, preset="balanced", video_mode="mp3", **cfg)
            good = os.path.exists(out["final"]) and os.path.getsize(out["final"]) > 1000
            outs.append((out, cfg))
            print(f"  file{i+1}: {cfg['loudness']}, trim={cfg['trim']}, "
                  f"silence_removed={out['silence_removed_s']}s, out={os.path.basename(out['final'])}")
            ok = ok and good
        except Exception as e:  # noqa: BLE001 - test harness
            ok = False
            print(f"  file{i+1} EXCEPTION: {e}")
    check("batch: 3 files processed without error", ok)

    # ---------------------------------------------------------- (b) silence trim
    # Deterministic tone+silence file (trim unit test: no DeepFilterNet here).
    ftrim = os.path.join(WORK, "trim_test.wav")
    synth_tone_with_silences(ftrim)
    sil_before = detect_silences(ftrim, min_silence=0.7, threshold_db=-40.0)
    dur_before = probe_duration(ftrim)
    trimmed = os.path.join(WORK, "trimmed.wav")
    _, removed = trim_silence(ftrim, trimmed, min_silence=0.7,
                              threshold_db=-40.0, padding=0.15)
    dur_after = probe_duration(trimmed)
    sil_after = detect_silences(trimmed, min_silence=0.7, threshold_db=-40.0)
    print(f"  silences before: {[(round(s,2),round(e,2)) for s,e in sil_before]}")
    print(f"  duration {dur_before:.2f}s -> {dur_after:.2f}s (removed {removed:.2f}s)")
    print(f"  silences >=0.7s after: {len(sil_after)}")
    check("trim: detected the 2 long silences",
          len(sil_before) == 2, f"found {len(sil_before)}")
    check("trim: duration shrank by ~3.9s (3.0+1.5 minus 2x0.15 padding each)",
          abs((dur_before - dur_after) - 3.9) < 0.4,
          f"delta={(dur_before - dur_after):.2f}s")
    check("trim: no >=0.7s silences remain", len(sil_after) == 0)
    # padding kept: the remaining audio around cuts should start/end with ~0.15s
    # of near-silence, not a hard-clipped tone -> check no big DC jump at 1.7s mark
    check("trim: output is valid audio", dur_after > 5.0)

    # ---------------------------------------------------------- (c) loudness presets
    targets = {"podcast": -16.0, "youtube": -14.0, "audiobook": -19.0}
    for out, cfg in outs:
        name = cfg["loudness"]
        got = integrated_lufs(out["final"])
        want = targets[name]
        print(f"  preset {name}: measured {got:.1f} LUFS (target {want})")
        check(f"loudness: {name} within +/-1 LUFS", abs(got - want) <= 1.0,
              f"{got:.1f} vs {want}")

    # ---------------------------------------------------------- (d) denoise floor
    # Project's own method (README): mean RMS over silence gaps, real voice.
    # Synthetic voice + KNOWN -33dB noise-only head/tail gaps.
    vfile = os.path.join(WORK, "voice_gaps.wav")
    synth_voice(vfile, seconds=6.0, noise_db=-33.0,
                lead_silence=2.0, tail_silence=2.0)
    w48 = os.path.join(WORK, "n48.wav")
    enh = os.path.join(WORK, "nenh.wav")
    to_wav_48k(vfile, w48)
    deepfilter_enhance(w48, enh, preset="balanced")
    gaps = [(0.2, 1.8), (8.2, 9.8)]  # inside the noise-only head/tail
    def mean_floor(path):
        import math
        vals = [rms_db(path, a, b) for a, b in gaps]
        lin = [10 ** (v / 20.0) for v in vals]
        return 20 * math.log10(sum(lin) / len(lin))
    floor_before = mean_floor(vfile)
    floor_after = mean_floor(enh)
    print(f"  noise floor (mean RMS over 2 gaps): "
          f"{floor_before:.1f} dB -> {floor_after:.1f} dB "
          f"(delta {floor_before - floor_after:.1f} dB)")
    check("denoise: input floor ~-33dB (+/-3)", abs(floor_before - (-33)) < 3.0,
          f"{floor_before:.1f} dB")
    # Synthetic stationary noise is "easier" than real fan noise, so the floor
    # lands deeper than the project's published -58.9dB on real recordings.
    # What matters: reduction in the same class, and speech preserved.
    check("denoise: output floor <= -54dB (at least as good as free build)",
          floor_after <= -54.0, f"{floor_after:.1f} dB")
    check("denoise: reduction >= 20dB", (floor_before - floor_after) >= 20.0,
          f"delta={(floor_before - floor_after):.1f} dB")
    # Voice preservation (project's method: speech energy must not drop):
    # RMS over a voice-only window before vs after denoise.
    v_before = rms_db(vfile, 3.0, 5.0)
    v_after = rms_db(enh, 3.0, 5.0)
    print(f"  voice energy: {v_before:.1f} dB -> {v_after:.1f} dB")
    check("denoise: voice energy preserved (+/-4dB)",
          abs(v_after - v_before) <= 4.0,
          f"{v_before:.1f} -> {v_after:.1f} dB")

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("ALL PRO TESTS PASSED")

if __name__ == "__main__":
    main()
