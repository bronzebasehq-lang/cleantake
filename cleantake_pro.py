"""CleanTake Pro v1 — paid companion to the free CleanTake app.

Same local-only web UI pattern (bind 127.0.0.1, nothing leaves the machine),
plus the four Pro features:
  1. Batch queue      — many files at once, sequential, per-file progress.
  2. Watch-folder     — point at a folder; new files are processed automatically
                        while the app stays open.
  3. Silence trimming — cuts long silences (ffmpeg silencedetect) after denoise.
  4. Loudness presets — Podcast (-16 LUFS) / YouTube (-14) / Audiobook (-19).

License: a license.key file must sit next to the exe (or repo root in dev)
containing a valid CT-PRO-XXXX-XXXX key. Without one, every page redirects
to /license where the key can be pasted. Keys are issued after purchase
(Lemon Squeezy); the dev key in pro/DEV_LICENSE_KEY.txt is for testing only.
"""
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
import webbrowser
import zipfile

from flask import Flask, request, send_file, render_template_string, redirect

from pipeline import PRESETS
from pipeline_pro import LOUDNESS_PRESETS, process_file_pro
from license import is_licensed, activate_key, clear_saved_key

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "pro_uploads")
OUTPUTS = os.path.join(BASE, "pro_outputs")
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(OUTPUTS, exist_ok=True)

ALLOWED = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wma",
           ".mp4", ".mov", ".mkv", ".webm", ".avi"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB per batch

VERSION = "1.0.5"
WATCH_STATE_FILE = os.path.join(OUTPUTS, "watch_state.json")

# ---------------------------------------------------------------- styles/pages

PAGE_CSS = """
body{font-family:system-ui,sans-serif;max-width:680px;margin:48px auto;padding:0 20px;color:#1a1a1a;background:#fafafa}
h1{font-size:28px;margin-bottom:4px} .sub{color:#666;margin-bottom:28px}
.card{background:#fff;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 1px 4px #0001}
button,.btn{background:#111;color:#fff;border:0;border-radius:8px;padding:12px 28px;font-size:16px;cursor:pointer;text-decoration:none;display:inline-block}
button:disabled{opacity:.4;cursor:wait}
.note{color:#777;font-size:13px;margin-top:24px;line-height:1.6}
.quitbtn{background:none !important;border:1px solid #ccc !important;color:#999 !important;padding:8px 20px !important;font-size:13px !important;border-radius:8px;cursor:pointer}
.ver{color:#bbb;font-size:11px;margin-top:18px;text-align:center}
.pro-badge{display:inline-block;background:#111;color:#ffd34d;font-size:12px;font-weight:700;border-radius:6px;padding:3px 10px;margin-left:8px;vertical-align:middle}
input[type=text],input[type=number]{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px;font-size:14px;box-sizing:border-box}
"""

QUIT_FORM = """
<form method="post" action="/quit" style="margin-top:30px;text-align:center">
<button type="submit" class="quitbtn">⏻ Quit CleanTake Pro</button>
</form>
"""

LICENSE_HTML = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanTake Pro — license</title>
<style>""" + PAGE_CSS + """
.err{background:#fef3f2;border:1px solid #f5c6c0;border-radius:8px;padding:12px;font-size:14px;color:#7a271a;margin:12px 0}
</style></head><body>
<h1><img src="data:image/png;base64,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" width="40" height="40" style="border-radius:10px;vertical-align:-9px;margin-right:6px" alt="CleanTake logo">CleanTake <span class="pro-badge">PRO</span></h1>
<div class="sub">Enter your license key to unlock the Pro features.<br>
<span style="font-size:13px">Keys are issued by Lemon Squeezy after purchase — paste the key from your receipt email. (Older CT-PRO-XXXX-XXXX keys also work.)</span></div>
<div class="card">
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<form method="post" action="/license">
<input type="text" name="key" placeholder="paste your license key" autocomplete="off"
       style="font-family:monospace;font-size:18px;text-align:center;letter-spacing:2px">
<div style="text-align:center;margin-top:14px"><button type="submit">Unlock Pro ✨</button></div>
</form>
</div>
<div class="note">Your key is stored in <b>license.key</b> next to the app. It is checked against Lemon Squeezy's license server when you enter it (and re-checked daily) — nothing else leaves your computer. Lost your key? Re-download it from your purchase email.</div>
</body></html>
"""

INDEX_HTML = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanTake Pro — one file in, one file out</title>
<style>""" + PAGE_CSS + """
#drop{border:2px dashed #999;border-radius:12px;padding:48px 20px;text-align:center;background:#fff;cursor:pointer}
#drop.drag{border-color:#111;background:#f0f0f0}
.presets{display:flex;gap:10px;margin:18px 0 6px}
.preset{flex:1;border:2px solid #ddd;border-radius:10px;padding:12px;cursor:pointer;background:#fff;font-size:14px}
.preset input{display:none}
.preset.sel{border-color:#111;background:#f5f5f5}
.preset b{display:block;margin-bottom:4px}
.preset span{color:#666;font-size:13px}
.sect{font-weight:600;margin:20px 0 4px}
.vidmode label{display:block;background:#fff;border-radius:8px;padding:10px 12px;margin:6px 0;cursor:pointer;border:1px solid #e3e3e3;font-size:14px}
#filelist{margin-top:10px;font-size:14px;color:#444}
.chk{display:block;background:#fff;border-radius:8px;padding:10px 12px;margin:6px 0;cursor:pointer;border:1px solid #e3e3e3;font-size:14px}
.watch-on{background:#f0fbf0;border:1px solid #bfe6bf}
.meta{color:#666;font-size:13px}
</style></head><body>
<h1><img src="data:image/png;base64,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" width="40" height="40" style="border-radius:10px;vertical-align:-9px;margin-right:6px" alt="CleanTake logo">CleanTake <span class="pro-badge">PRO</span></h1>
<div class="sub">One file in, one file out. Noise gone. 100% local — nothing is uploaded anywhere.</div>

<form id="f" method="post" action="/process" enctype="multipart/form-data">
<div id="drop">Drop audio or video files here<br><span style="color:#888;font-size:14px">or click to choose (many at once is fine)</span></div>
<input type="file" id="file" name="files" accept="audio/*,video/*" multiple style="display:none">
<div id="filelist"></div>

<div class="sect">1 · Cleanup strength</div>
<div class="presets">
<label class="preset" id="p-gentle"><input type="radio" name="preset" value="gentle"><b>🌿 Gentle</b><span>Light cleanup. Keeps a little room tone.</span></label>
<label class="preset" id="p-balanced"><input type="radio" name="preset" value="balanced" checked><b>⚖️ Balanced</b><span>Standard cleanup. The default.</span></label>
<label class="preset" id="p-strong"><input type="radio" name="preset" value="strong"><b>💪 Strong</b><span>Aggressive, for very noisy recordings.</span></label>
</div>

<div class="sect">2 · Loudness target (1-click)</div>
<div class="presets">
<label class="preset" id="l-podcast"><input type="radio" name="loudness" value="podcast" checked><b>🎙️ Podcast</b><span>−16 LUFS. Spotify / Apple Podcasts.</span></label>
<label class="preset" id="l-youtube"><input type="radio" name="loudness" value="youtube"><b>▶️ YouTube</b><span>−14 LUFS. YouTube normalization.</span></label>
<label class="preset" id="l-audiobook"><input type="radio" name="loudness" value="audiobook"><b>📖 Audiobook</b><span>−19 LUFS. ACX-style.</span></label>
</div>

<div class="sect">3 · Silence trimming</div>
<label class="chk"><input type="checkbox" name="trim" value="1" checked>
✂️ Cut silences longer than
<input type="number" name="min_silence" value="0.7" min="0.3" max="5" step="0.1"
       style="width:70px;display:inline"> s <span class="meta">(keeps 0.15 s breathing room each side)</span></label>

<div class="sect">For video files:</div>
<div class="vidmode">
<label><input type="radio" name="video_mode" value="mp3"> 🎵 MP3 audio only</label>
<label><input type="radio" name="video_mode" value="video" checked> 🎬 Video with clean audio <span style="color:#666">(recommended)</span></label>
</div>

<div style="text-align:center"><button id="go" type="submit" disabled>Clean them ✨</button></div>
</form>

<div class="card" id="watchcard">
<div class="sect" style="margin-top:0">👁️ Watch-folder <span class="meta">(auto-process new files)</span></div>
{% if watch.running %}
<div class="meta">Watching: <b>{{ watch.folder }}</b> — {{ watch.processed }} file(s) processed.
Keep this app open; new files are picked up every few seconds.</div>
<div class="meta">Last: {{ watch.last or "—" }}</div>
<form method="post" action="/watch/stop" style="margin-top:10px">
<button type="submit" class="quitbtn">Stop watching</button></form>
{% else %}
<form method="post" action="/watch/start">
<input type="text" name="folder" placeholder="Folder path, e.g. C:\\Podcasts\\Inbox">
<div class="meta" style="margin:8px 0">New audio/video files dropped in this folder are cleaned
automatically with the settings above, into <b>CleanTake-Pro-Output</b> inside it.</div>
<div style="text-align:center"><button type="submit">Start watching 👀</button></div>
</form>
{% endif %}
</div>

<div class="note">Supported: mp3, wav, m4a, ogg, flac, mp4, mov, webm…<br>
Pipeline: DeepFilterNet → silence trim → loudness preset.<br><br>
<i>Best for steady background noise (fan, hum, room tone). Can't separate overlapping voices, e.g. café chatter.</i></div>
""" + QUIT_FORM + """
<div class="ver">CleanTake Pro v""" + VERSION + """ · 100% local · <a href="#" onclick="event.preventDefault();fetch('/license/deactivate',{method:'POST'}).then(()=>location.href='/license')">change license key</a></div>
<script>
const drop=document.getElementById('drop'),inp=document.getElementById('file'),
      go=document.getElementById('go'),f=document.getElementById('f'),
      filelist=document.getElementById('filelist');
drop.onclick=()=>inp.click();
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('drag')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('drag')}));
drop.addEventListener('drop',ev=>{inp.files=ev.dataTransfer.files;picked()});
inp.onchange=picked;
function picked(){
  if(inp.files.length){
    filelist.innerHTML='📁 '+inp.files.length+' file(s): '+[...inp.files].map(x=>x.name).join(', ');
    go.disabled=false;
  }
}
f.onsubmit=()=>{if(!inp.files.length)return false;go.disabled=true;go.textContent='Uploading…';return true};
document.querySelectorAll('.preset').forEach(p=>p.addEventListener('click',()=>{
  const grp=p.parentElement;
  grp.querySelectorAll('.preset').forEach(x=>x.classList.remove('sel'));p.classList.add('sel')}));
document.getElementById('p-balanced').classList.add('sel');
document.getElementById('l-podcast').classList.add('sel');
</script></body></html>
"""

PROGRESS_HTML = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanTake Pro — working…</title>
<style>""" + PAGE_CSS + """
.bar{height:14px;background:#e8e8e8;border-radius:7px;overflow:hidden;margin:18px 0}
.bar>div{height:100%;background:#111;width:0%;transition:width .4s}
</style></head><body>
<h1>⏳ Cleaning your files…</h1>
<div class="sub">This runs locally on your machine. You can keep this tab open.</div>
<div class="card">
<div class="bar"><div id="fill"></div></div>
<div id="txt">Starting…</div>
</div>
<a href="/">← Cancel and go back</a>
<script>
const id="{{ job_id }}";
const fill=document.getElementById('fill'),txt=document.getElementById('txt');
const t=setInterval(async()=>{
  try{
    const r=await fetch('/job/'+id+'/status');const s=await r.json();
    const pct=s.total?Math.round(100*s.done/s.total):0;
    fill.style.width=pct+'%';
    txt.textContent=s.finished?('Done — '+s.done+'/'+s.total+' processed'):
      ('File '+Math.min(s.done+1,s.total)+' of '+s.total+': '+(s.current_name||'…'));
    if(s.finished){clearInterval(t);location.href='/job/'+id+'/result'}
  }catch(e){txt.textContent='Connection hiccup… still working.'}
},1000);
</script></body></html>
"""

RESULT_HTML = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanTake Pro — done</title>
<style>""" + PAGE_CSS + """
audio,video{width:100%} .ok{color:#1a7f37;font-weight:600} .bad{color:#b42318;font-weight:600}
.meta{color:#666;font-size:13px} .err{background:#fef3f2;border:1px solid #f5c6c0;border-radius:8px;padding:12px;font-size:14px;color:#7a271a}
.row{display:flex;gap:16px} .row>div{flex:1} @media(max-width:560px){.row{flex-direction:column}}
.stat{background:#f6f6f6;border-radius:6px;padding:6px 10px;font-size:13px;color:#444;margin-top:8px}
</style></head><body>
<h1>✅ Done — hear the difference</h1>
<div class="meta">{{ done }} of {{ total }} file(s) cleaned · {{ preset }} · {{ loudness }}
· trim {{ "on" if trim else "off" }} · took {{ secs|round(1) }}s</div>
{% if zip_ok %}<br><a class="btn" href="/job/{{ job_id }}/zip">⬇ Download all (.zip)</a>{% endif %}
{% for it in items %}
<div class="card">
<b>{{ it.name }}</b> —
{% if it.ok %}<span class="ok">cleaned ✓</span>
<div class="stat">✂️ Silence removed: {{ it.silence_removed_s }}s · 🔊 {{ it.loudness_label }}</div>
<div class="row" style="margin-top:12px">
<div><div class="meta">🔴 Before</div><audio controls src="/file/{{ it.before_tok }}"></audio></div>
<div><div class="meta">🟢 After</div>
{% if it.is_video %}<video controls src="/file/{{ it.after_tok }}"></video>
{% else %}<audio controls src="/file/{{ it.after_tok }}"></audio>{% endif %}</div>
</div>
<div style="margin-top:10px"><a class="btn" href="/download/{{ it.after_tok }}">⬇ Download</a></div>
{% else %}<span class="bad">failed ✗</span>
<div class="err" style="margin-top:10px">{{ it.error }}</div>
{% endif %}
</div>
{% endfor %}
<br><a href="/">← Clean more files</a>
<div class="note"><i>Best for steady background noise (fan, hum, room tone). Can't separate overlapping voices, e.g. café chatter.</i></div>
""" + QUIT_FORM + """
</body></html>
"""

QUIT_HTML = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanTake Pro — quit</title>
<style>""" + PAGE_CSS + """</style></head><body>
<h1>👋 CleanTake Pro đã tắt</h1>
<div class="sub">CleanTake Pro has quit. You can safely close this tab.</div>
<div class="card">Mở lại bằng cách nhấp đúp <b>CleanTake-Pro.exe</b>.<br>
<span style="color:#666">To run again, double-click <b>CleanTake-Pro.exe</b>.</span></div>
</body></html>
"""

FILES = {}
JOBS = {}
JOBS_LOCK = threading.Lock()

WATCH = {"running": False, "folder": "", "processed": 0, "last": "",
         "settings": {}}
WATCH_LOCK = threading.Lock()
WATCH_SEEN = set()  # absolute paths already handled


def _register(path):
    tok = uuid.uuid4().hex
    FILES[tok] = path
    return tok


def _watch_state_path():
    return WATCH_STATE_FILE


def _load_watch_seen():
    try:
        with open(_watch_state_path(), "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen", []))
    except OSError:
        return set()


def _save_watch_seen():
    try:
        with open(_watch_state_path(), "w", encoding="utf-8") as f:
            json.dump({"seen": sorted(WATCH_SEEN)[-2000:]}, f)
    except OSError:
        pass


def _watcher():
    """Background loop: every 5s, process new files in the watch folder."""
    while True:
        time.sleep(5)
        with WATCH_LOCK:
            running = WATCH["running"]
            folder = WATCH["folder"]
            settings = dict(WATCH["settings"])
        if not running or not folder or not os.path.isdir(folder):
            continue
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        outdir = os.path.join(folder, "CleanTake-Pro-Output")
        os.makedirs(outdir, exist_ok=True)
        for name in names:
            src = os.path.join(folder, name)
            if not os.path.isfile(src):
                continue
            if os.path.splitext(name)[1].lower() not in ALLOWED:
                continue
            if src in WATCH_SEEN:
                continue
            WATCH_SEEN.add(src)
            _save_watch_seen()
            try:
                workdir = os.path.join(OUTPUTS, "watch_" + uuid.uuid4().hex[:8])
                os.makedirs(workdir, exist_ok=True)
                out = process_file_pro(src, workdir, **settings)
                base = os.path.splitext(name)[0]
                ext = os.path.splitext(out["final"])[1]
                dst = os.path.join(outdir, f"{base}_clean{ext}")
                # avoid overwriting an existing output
                i = 2
                while os.path.exists(dst):
                    dst = os.path.join(outdir, f"{base}_clean_{i}{ext}")
                    i += 1
                # shutil.move (not os.replace): watch folder may be on a
                # different drive than the app -> cross-device safe.
                shutil.move(out["final"], dst)
                ok, msg = True, name
            except Exception as e:  # noqa: BLE001 - watcher must never die
                ok, msg = False, f"{name}: {str(e)[:120]}"
            with WATCH_LOCK:
                WATCH["processed"] += 1 if ok else 0
                WATCH["last"] = ("✓ " if ok else "✗ ") + msg


def _worker(job_id, file_jobs, settings):
    """Process files sequentially; one bad file never kills the batch."""
    t0 = time.time()
    for fj in file_jobs:
        with JOBS_LOCK:
            JOBS[job_id]["current_name"] = fj["name"]
        try:
            out = process_file_pro(fj["src"], fj["workdir"], **settings)
            fj["ok"] = True
            fj["after_tok"] = _register(out["final"])
            fj["is_video"] = out["final"].lower().endswith(".mp4")
            fj["silence_removed_s"] = out["silence_removed_s"]
            fj["loudness_label"] = "%s (-%d LUFS)" % (
                settings["loudness"].title(),
                abs(LOUDNESS_PRESETS[settings["loudness"]][0]))
        except Exception:  # noqa: BLE001 - per-file isolation, friendly message
            fj["ok"] = False
            fj["error"] = ("Couldn't process this file — it may be corrupted "
                           "or have no audio track.")
        with JOBS_LOCK:
            JOBS[job_id]["done"] += 1
    with JOBS_LOCK:
        JOBS[job_id]["finished"] = True
        JOBS[job_id]["secs"] = time.time() - t0


def _form_settings(form):
    preset = form.get("preset", "balanced")
    if preset not in PRESETS:
        preset = "balanced"
    loudness = form.get("loudness", "podcast")
    if loudness not in LOUDNESS_PRESETS:
        loudness = "podcast"
    trim = form.get("trim") == "1"
    try:
        min_silence = float(form.get("min_silence", "0.7"))
    except ValueError:
        min_silence = 0.7
    min_silence = min(5.0, max(0.3, min_silence))
    video_mode = form.get("video_mode", "video")
    if video_mode not in ("mp3", "video"):
        video_mode = "video"
    return {"preset": preset, "loudness": loudness, "trim": trim,
            "min_silence": min_silence, "video_mode": video_mode}


@app.before_request
def _license_gate():
    if request.path == "/license" or request.path.startswith("/static"):
        return None
    if not is_licensed():
        return redirect("/license")
    return None


@app.route("/license", methods=["GET", "POST"])
def license_page():
    error = ""
    if request.method == "POST":
        key = (request.form.get("key") or "").strip()
        if key:
            ok, msg = activate_key(key)
            if ok:
                return redirect("/")
            error = msg
        else:
            error = "Please enter your license key."
    return render_template_string(LICENSE_HTML, error=error)


@app.route("/license/deactivate", methods=["POST"])
def license_deactivate():
    """Forget the saved key so a different one can be entered."""
    clear_saved_key()
    return redirect("/license")


@app.route("/")
def index():
    with WATCH_LOCK:
        watch = dict(WATCH)
    return render_template_string(INDEX_HTML, watch=watch)


@app.route("/process", methods=["POST"])
def process():
    files = request.files.getlist("files")
    files = [f for f in files if f and f.filename]
    if not files:
        return "No files uploaded", 400
    settings = _form_settings(request.form)

    job_id = uuid.uuid4().hex[:12]
    workdir = os.path.join(OUTPUTS, job_id)
    os.makedirs(workdir, exist_ok=True)

    file_jobs = []
    for i, f in enumerate(files):
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED:
            file_jobs.append({"name": f.filename, "rejected": True, "ext": ext})
            continue
        safe = f"{i:02d}_{f.filename}"
        safe = "".join(c for c in safe if c.isalnum() or c in "._- ")
        src = os.path.join(workdir, safe)
        f.save(src)
        file_jobs.append({"name": f.filename, "src": src,
                          "workdir": workdir, "before_tok": _register(src)})

    with JOBS_LOCK:
        JOBS[job_id] = {"total": len(file_jobs), "done": 0,
                        "current_name": "", "finished": False,
                        "items": file_jobs, "settings": settings, "secs": 0}

    pending = []
    for fj in file_jobs:
        if fj.get("rejected"):
            fj["ok"] = False
            fj["error"] = (f"Unsupported format ({fj['ext']}) — "
                           "try mp3, wav, m4a, ogg, flac or mp4.")
            with JOBS_LOCK:
                JOBS[job_id]["done"] += 1
        else:
            pending.append(fj)

    if pending:
        th = threading.Thread(target=_worker,
                              args=(job_id, pending, settings),
                              daemon=True)
        th.start()
    else:
        with JOBS_LOCK:
            JOBS[job_id]["finished"] = True

    return render_template_string(PROGRESS_HTML, job_id=job_id)


@app.route("/watch/start", methods=["POST"])
def watch_start():
    folder = (request.form.get("folder") or "").strip().strip('"')
    if not folder or not os.path.isdir(folder):
        return ("That folder doesn't exist — check the path and try again. "
                "Example: C:\\Podcasts\\Inbox"), 400
    settings = _form_settings(request.form)
    global WATCH_SEEN
    WATCH_SEEN = _load_watch_seen()
    with WATCH_LOCK:
        WATCH.update({"running": True, "folder": folder,
                      "settings": settings})
    return redirect("/")


@app.route("/watch/stop", methods=["POST"])
def watch_stop():
    with WATCH_LOCK:
        WATCH["running"] = False
    return redirect("/")


@app.route("/job/<job_id>/status")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return {"error": "unknown job"}, 404
        return {"total": job["total"], "done": job["done"],
                "current_name": job["current_name"],
                "finished": job["finished"]}


@app.route("/job/<job_id>/result")
def job_result(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return "Unknown job", 404
        if not job["finished"]:
            return render_template_string(PROGRESS_HTML, job_id=job_id)
        items = list(job["items"])
        secs, settings = job["secs"], job["settings"]
    done = sum(1 for it in items if it.get("ok"))
    zip_ok = done > 0
    return render_template_string(RESULT_HTML, items=items, done=done,
                                  total=len(items), secs=secs,
                                  preset=settings["preset"],
                                  loudness=settings["loudness"].title(),
                                  trim=settings["trim"],
                                  zip_ok=zip_ok, job_id=job_id)


@app.route("/job/<job_id>/zip")
def job_zip(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return "Unknown job", 404
        paths = [(it["name"], FILES.get(it.get("after_tok")))
                 for it in job["items"] if it.get("ok")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in paths:
            if path and os.path.exists(path):
                base = os.path.splitext(name)[0]
                ext = os.path.splitext(path)[1]
                z.write(path, f"{base}_clean{ext}")
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="cleantake_pro_batch.zip",
                     mimetype="application/zip")


@app.route("/file/<tok>")
def serve(tok):
    path = FILES.get(tok)
    if not path or not os.path.exists(path):
        return "Not found", 404
    return send_file(path)


@app.route("/download/<tok>")
def download(tok):
    path = FILES.get(tok)
    if not path or not os.path.exists(path):
        return "Not found", 404
    base = os.path.splitext(os.path.basename(path))[0]
    base = re.sub(r"^\d{2}_", "", base)
    ext = os.path.splitext(path)[1]
    return send_file(path, as_attachment=True,
                     download_name=f"{base}{ext}")


@app.route("/quit", methods=["POST"])
def quit_app():
    def _halt():
        time.sleep(0.8)
        os._exit(0)

    threading.Thread(target=_halt, daemon=True).start()
    return render_template_string(QUIT_HTML)


HOST, PORT = "127.0.0.1", 5058  # Pro uses its own port (free app is on 5057)


def _port_free():
    import socket
    s = socket.socket()
    try:
        s.bind((HOST, PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _alert(title, text):
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x30)
        except Exception:
            pass
    else:
        print(f"{title}: {text}", file=sys.stderr)


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    try:
        if not _port_free():
            _alert("CleanTake Pro",
                   "CleanTake Pro is already running.\n\n"
                   "Please quit the other copy first (use the \u201cQuit "
                   "CleanTake Pro\u201d button on its web page), then start "
                   "CleanTake Pro again.")
            sys.exit(1)
        threading.Thread(target=_watcher, daemon=True).start()
        print(f"CleanTake Pro v{VERSION} running at {url}  (local only)")
        if getattr(sys, "frozen", False):
            webbrowser.open(url)
        app.run(host=HOST, port=PORT, debug=False)
    except OSError as e:
        _alert("CleanTake Pro",
               f"CleanTake Pro could not start.\n\n{e}\n\n"
               "The network port may be in use by another program.")
        sys.exit(1)
