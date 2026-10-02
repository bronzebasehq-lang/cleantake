"""CleanTake web UI. Local only: bind 127.0.0.1, no data leaves the machine.

Batch mode: upload many files at once, they are processed one by one in a
background thread; the browser polls /job/<id>/status for progress.
"""
import io
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
import zipfile

from flask import Flask, request, send_file, render_template_string
from werkzeug.utils import secure_filename

from pipeline import process_file, PRESETS

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
OUTPUTS = os.path.join(BASE, "outputs")
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(OUTPUTS, exist_ok=True)

ALLOWED = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wma",
           ".mp4", ".mov", ".mkv", ".webm", ".avi"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB per batch

VERSION = "1.0.1"

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
"""

QUIT_FORM = """
<form method="post" action="/quit" style="margin-top:30px;text-align:center">
<button type="submit" class="quitbtn">⏻ Quit CleanTake</button>
</form>
"""

INDEX_HTML = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanTake — one file in, one file out</title>
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
</style></head><body>
<h1>🎙️ CleanTake</h1>
<div class="sub">One file in, one file out. Noise gone. 100% local — nothing is uploaded anywhere.</div>
<form id="f" method="post" action="/process" enctype="multipart/form-data">
<div id="drop">Drop audio or video files here<br><span style="color:#888;font-size:14px">or click to choose (many at once is fine)</span></div>
<input type="file" id="file" name="files" accept="audio/*,video/*" multiple style="display:none">
<div id="filelist"></div>

<div class="sect">Cleanup strength</div>
<div class="presets">
<label class="preset" id="p-gentle"><input type="radio" name="preset" value="gentle"><b>🌿 Gentle</b><span>Light cleanup. Keeps a little room tone so voices sound natural.</span></label>
<label class="preset" id="p-balanced"><input type="radio" name="preset" value="balanced" checked><b>⚖️ Balanced</b><span>Standard cleanup. The default.</span></label>
<label class="preset" id="p-strong"><input type="radio" name="preset" value="strong"><b>💪 Strong</b><span>Aggressive noise removal for very noisy recordings.</span></label>
</div>

<div class="sect">For video files:</div>
<div class="vidmode">
<label><input type="radio" name="video_mode" value="mp3"> 🎵 MP3 audio only</label>
<label><input type="radio" name="video_mode" value="video" checked> 🎬 Video with clean audio <span style="color:#666">(recommended)</span></label>
</div>

<div style="text-align:center"><button id="go" type="submit" disabled>Clean them ✨</button></div>
</form>
<div class="note">Supported: mp3, wav, m4a, ogg, flac, mp4, mov, webm…<br>
Pipeline: DeepFilterNet noise removal → loudness leveling to −16 LUFS. Replaces Descript Studio Sound / Krisp / Auphonic — for $0.<br><br>
<i>Best for steady background noise (fan, hum, room tone). Can't separate overlapping voices, e.g. café chatter.</i></div>
""" + QUIT_FORM + """
<div class="ver">CleanTake v""" + VERSION + """ · 100% local</div>
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
  document.querySelectorAll('.preset').forEach(x=>x.classList.remove('sel'));p.classList.add('sel')}));
document.getElementById('p-balanced').classList.add('sel');
</script></body></html>
"""

PROGRESS_HTML = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CleanTake — working…</title>
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
<title>CleanTake — done</title>
<style>""" + PAGE_CSS + """
audio,video{width:100%} .ok{color:#1a7f37;font-weight:600} .bad{color:#b42318;font-weight:600}
.meta{color:#666;font-size:13px} .err{background:#fef3f2;border:1px solid #f5c6c0;border-radius:8px;padding:12px;font-size:14px;color:#7a271a}
.row{display:flex;gap:16px} .row>div{flex:1} @media(max-width:560px){.row{flex-direction:column}}
</style></head><body>
<h1>✅ Done — hear the difference</h1>
<div class="meta">{{ done }} of {{ total }} file(s) cleaned · preset: {{ preset }} · took {{ secs|round(1) }}s</div>
{% if zip_ok %}<br><a class="btn" href="/job/{{ job_id }}/zip">⬇ Download all (.zip)</a>{% endif %}
{% for it in items %}
<div class="card">
<b>{{ it.name }}</b> —
{% if it.ok %}<span class="ok">cleaned ✓</span>
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
<title>CleanTake — quit</title>
<style>""" + PAGE_CSS + """</style></head><body>
<h1>👋 CleanTake đã tắt</h1>
<div class="sub">CleanTake has quit. You can safely close this tab.</div>
<div class="card">Mở lại bằng cách nhấp đúp <b>CleanTake.exe</b>.<br>
<span style="color:#666">To run again, double-click <b>CleanTake.exe</b>.</span></div>
</body></html>
"""

# token -> path registry (in-memory, local only)
FILES = {}
# job_id -> job state (in-memory, local only)
JOBS = {}
JOBS_LOCK = threading.Lock()


def _register(path):
    tok = uuid.uuid4().hex
    FILES[tok] = path
    return tok


def _worker(job_id, file_jobs, preset, video_mode):
    """Process files sequentially; one bad file never kills the batch."""
    t0 = time.time()
    for fj in file_jobs:
        with JOBS_LOCK:
            JOBS[job_id]["current_name"] = fj["name"]
        try:
            out = process_file(fj["src"], fj["workdir"],
                               preset=preset, video_mode=video_mode)
            fj["ok"] = True
            fj["after_tok"] = _register(out["final"])
            fj["is_video"] = out["final"].lower().endswith(".mp4")
        except Exception:  # noqa: BLE001 - per-file isolation, friendly message
            fj["ok"] = False
            fj["error"] = ("Couldn't process this file — it may be corrupted "
                           "or have no audio track.")
        with JOBS_LOCK:
            JOBS[job_id]["done"] += 1
    with JOBS_LOCK:
        JOBS[job_id]["finished"] = True
        JOBS[job_id]["secs"] = time.time() - t0


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/process", methods=["POST"])
def process():
    files = request.files.getlist("files")
    files = [f for f in files if f and f.filename]
    if not files:
        return "No files uploaded", 400
    preset = request.form.get("preset", "balanced")
    if preset not in PRESETS:
        preset = "balanced"
    video_mode = request.form.get("video_mode", "video")
    if video_mode not in ("mp3", "video"):
        video_mode = "video"

    job_id = uuid.uuid4().hex[:12]
    workdir = os.path.join(OUTPUTS, job_id)
    os.makedirs(workdir, exist_ok=True)

    file_jobs = []
    for i, f in enumerate(files):
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED:
            file_jobs.append({"name": secure_filename(f.filename),
                              "rejected": True, "ext": ext})
            continue
        safe = f"{i:02d}_{secure_filename(f.filename)}"
        src = os.path.join(workdir, safe)
        f.save(src)
        file_jobs.append({"name": secure_filename(f.filename), "src": src,
                          "workdir": workdir, "before_tok": _register(src)})

    with JOBS_LOCK:
        JOBS[job_id] = {"total": len(file_jobs), "done": 0,
                        "current_name": "", "finished": False,
                        "items": file_jobs, "preset": preset, "secs": 0}

    # Rejected-format files are marked failed immediately (no thread needed).
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
                              args=(job_id, pending, preset, video_mode),
                              daemon=True)
        th.start()
    else:
        with JOBS_LOCK:
            JOBS[job_id]["finished"] = True

    return render_template_string(PROGRESS_HTML, job_id=job_id)


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
        secs, preset = job["secs"], job["preset"]
    done = sum(1 for it in items if it.get("ok"))
    zip_ok = done > 0
    return render_template_string(RESULT_HTML, items=items, done=done,
                                  total=len(items), secs=secs, preset=preset,
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
                     download_name="cleantake_batch.zip",
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
    base = re.sub(r"^\d{2}_", "", base)  # strip batch numbering (00_song -> song)
    ext = os.path.splitext(path)[1]
    return send_file(path, as_attachment=True,
                     download_name=f"{base}{ext}")


@app.route("/quit", methods=["POST"])
def quit_app():
    """Shut the whole app down (the only clean exit once the console is hidden).

    Werkzeug 3.x removed werkzeug.server.shutdown, so we answer first and then
    kill the process from a daemon thread — the OS frees the port for us.
    """
    def _halt():
        time.sleep(0.8)  # let the quit page flush to the browser
        os._exit(0)

    threading.Thread(target=_halt, daemon=True).start()
    return render_template_string(QUIT_HTML)


HOST, PORT = "127.0.0.1", 5057


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
    """Native message box on Windows (there is no console in windowed mode)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x30)  # MB_ICONWARNING
        except Exception:
            pass
    else:
        print(f"{title}: {text}", file=sys.stderr)


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    try:
        if not _port_free():
            _alert("CleanTake",
                   "CleanTake is already running.\n\n"
                   "Please quit the other copy first (use the \u201cQuit "
                   "CleanTake\u201d button on its web page), then start "
                   "CleanTake again.")
            sys.exit(1)
        print(f"CleanTake v{VERSION} running at {url}  (local only)")
        # Open the browser only for the packaged app (PyInstaller), never in dev.
        if getattr(sys, "frozen", False):
            webbrowser.open(url)
        app.run(host=HOST, port=PORT, debug=False)
    except OSError as e:
        _alert("CleanTake",
               f"CleanTake could not start.\n\n{e}\n\n"
               "The network port may be in use by another program.")
        sys.exit(1)
