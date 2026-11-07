"""
Web interface for the Google Maps scraper.

Run:   python maps_web.py
Open:  http://127.0.0.1:5000

Login: put your username on line 1 and your password on line 2 of a file
called login.txt in this folder (keep it out of GitHub).
Optional host/port:
    $env:HOST = "0.0.0.0"; $env:PORT = "5000"
"""
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, fields

import pandas as pd
from flask import Flask, Response, jsonify, render_template_string, request, send_file

import maps_scraper as scraper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
jobs = {}
jobs_lock = threading.Lock()


LOGIN_SOURCE = ""
LOGIN_PROBLEM = ""
COMMON_PASSWORDS = {"password", "admin", "123456", "12345678", "qwerty", "letmein", "welcome", "changeme"}


def password_is_weak(pw: str) -> bool:
    return len(pw or "") < 12 or (pw or "").lower() in COMMON_PASSWORDS


def load_login():
    """Read the login from login.txt next to this file (line 1 = username, line 2 = password).
    Falls back to the APP_USERNAME / APP_PASSWORD environment variables.
    A bad login file never stops the app from starting; it is reported when the app starts."""
    global LOGIN_SOURCE, LOGIN_PROBLEM
    path = os.path.join(BASE_DIR, "login.txt")
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                text = raw.decode("utf-16")
            else:
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("cp1252")
            lines = [line for line in text.splitlines() if line != ""]
            if len(lines) >= 2:
                LOGIN_SOURCE = "login.txt"
                return lines[0], lines[1]
            LOGIN_PROBLEM = "login.txt was found but it needs two lines: the username, then the password."
        except Exception as e:  # noqa: BLE001
            LOGIN_PROBLEM = f"login.txt could not be read ({e})."
    password = os.environ.get("APP_PASSWORD")
    if password:
        LOGIN_SOURCE = "environment settings"
    return os.environ.get("APP_USERNAME", "admin"), password


USERNAME, PASSWORD = load_login()
COLUMNS = [f.name for f in fields(scraper.Place)]


def same(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


@app.before_request
def require_login():
    if not PASSWORD:
        return None
    auth = request.authorization
    if auth:
        user_ok = same(auth.username, USERNAME)
        pass_ok = same(auth.password, PASSWORD)
        if user_ok and pass_ok:
            return None
        # Say what went wrong in the black window (lengths only, never the values)
        reasons = []
        if not user_ok:
            reasons.append(f"username does not match (typed {len(auth.username or '')} characters, saved one has {len(USERNAME)})")
        if not pass_ok:
            reasons.append(f"password does not match (typed {len(auth.password or '')} characters, saved one has {len(PASSWORD)})")
        print("Login failed: " + "; ".join(reasons), flush=True)
    return Response(
        "Login required. Enter your username and password.",
        401,
        {"WWW-Authenticate": 'Basic realm="Maps Scraper"'},
    )


def stop_process(proc):
    """Stop the scraper process and the Chrome window it opened."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass


def watchdog(job, proc, idle_limit=240):
    """Stop a scraper that has printed nothing for a long time, so a stuck search can't block the tool."""
    while proc.poll() is None:
        time.sleep(5)
        if time.time() - job["last_output"] > idle_limit:
            job["timed_out"] = True
            stop_process(proc)
            return


def run_job(job_id: str, query: str, total: int):
    """Run the scraper as its own process (the same way that works from the command line)."""
    job = jobs[job_id]
    out_path = os.path.join(tempfile.gettempdir(), f"maps_{job_id}.json")
    cmd = [
        sys.executable, "-u", os.path.join(BASE_DIR, "maps_scraper.py"),
        "-s", query, "-t", str(total), "--json-out", out_path,
    ]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    try:
        proc = subprocess.Popen(
            cmd, cwd=BASE_DIR, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1,
        )
        job["proc"] = proc
        job["last_output"] = time.time()
        threading.Thread(target=watchdog, args=(job, proc), daemon=True).start()
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            job["last_output"] = time.time()
            if line.startswith("PROGRESS "):
                try:
                    _, done, count = line.split()
                    job["done"], job["count"] = int(done), int(count)
                except ValueError:
                    pass
                continue
            job["log"].append(line[-300:])
            job["log"] = job["log"][-40:]
            print(f"[scraper] {line[-300:]}", flush=True)
        proc.wait()
        if job["cancelled"]:
            job["status"] = "cancelled"
        elif os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as f:
                job["rows"] = json.load(f)
            job["status"] = "done"
        else:
            job["status"] = "error"
            job["error"] = (
                "The search was stopped because it showed no activity for 4 minutes."
                if job.get("timed_out") else "The scraper stopped unexpectedly."
            )
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        job.pop("proc", None)
        try:
            os.remove(out_path)
        except OSError:
            pass


def job_dataframe(job) -> pd.DataFrame:
    return pd.DataFrame(job.get("rows", []), columns=COLUMNS)


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    try:
        total = int(data.get("total") or 20)
    except (TypeError, ValueError):
        total = 20
    total = max(1, min(total, 100))
    if not query:
        return jsonify(error="Please type what to search for."), 400
    with jobs_lock:
        if any(j["status"] == "running" for j in jobs.values()):
            return jsonify(error="A search is already running. Wait for it to finish."), 409
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = {
            "status": "running", "query": query, "total": total,
            "done": 0, "count": 0, "rows": [], "error": "",
            "log": [], "cancelled": False, "timed_out": False,
            "last_output": time.time(),
        }
    threading.Thread(target=run_job, args=(job_id, query, total), daemon=True).start()
    return jsonify(job_id=job_id)


@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="Unknown job"), 404
    return jsonify(
        status=job["status"], query=job["query"], done=job["done"],
        count=job["count"], error=job["error"], rows=job["rows"],
        columns=COLUMNS, log=job["log"][-8:],
    )


@app.route("/current")
def current():
    for jid, j in jobs.items():
        if j["status"] == "running":
            return jsonify(job_id=jid)
    return jsonify(job_id=None)


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="Unknown job"), 404
    proc = job.get("proc")
    if job["status"] == "running":
        job["cancelled"] = True
        if proc:
            stop_process(proc)
    return jsonify(ok=True)


@app.route("/download/<job_id>.csv")
def download_csv(job_id):
    job = jobs.get(job_id)
    if not job:
        return "Unknown job", 404
    buf = io.BytesIO(job_dataframe(job).to_csv(index=False).encode("utf-8-sig"))
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                     download_name="google-maps-results.csv")


@app.route("/download/<job_id>.xlsx")
def download_xlsx(job_id):
    job = jobs.get(job_id)
    if not job:
        return "Unknown job", 404
    buf = io.BytesIO()
    job_dataframe(job).to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="google-maps-results.xlsx",
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Google Maps Scraper</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#1d2433; --muted:#667085; --line:#e4e7ec; --accent:#1a73e8; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#12151b; --card:#1b2029; --text:#e8ebf0; --muted:#98a2b3; --line:#2b3340; --accent:#6ea8fe; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         background:var(--bg); color:var(--text); }
  main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 1.5rem; margin: 0 0 4px; }
  p.sub { color: var(--muted); margin: 0 0 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:16px; }
  form { display:flex; flex-wrap:wrap; gap:10px; align-items:end; }
  label { display:flex; flex-direction:column; gap:4px; font-size:.85rem; color:var(--muted); }
  input { padding:10px 12px; border:1px solid var(--line); border-radius:8px; font-size:1rem;
          background:var(--bg); color:var(--text); }
  input#query { min-width: 320px; }
  input#total { width: 90px; }
  button, a.btn { padding:10px 16px; border-radius:8px; border:0; background:var(--accent); color:#fff;
          font-size:1rem; cursor:pointer; text-decoration:none; display:inline-block; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  a.btn.secondary { background:transparent; color:var(--accent); border:1px solid var(--accent); }
  #msg { margin: 0 0 8px; }
  .bar { height:8px; background:var(--line); border-radius:99px; overflow:hidden; margin-top:8px; }
  .bar > div { height:100%; width:0; background:var(--accent); transition: width .3s; }
  .tablewrap { overflow-x:auto; }
  table { border-collapse:collapse; width:100%; font-size:.9rem; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); white-space:nowrap;
           max-width:280px; overflow:hidden; text-overflow:ellipsis; }
  th { color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--card); }
  .actions { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px; }
  .hidden { display:none; }
  button.secondary { background:transparent; color:var(--accent); border:1px solid var(--accent); }
  pre#log { font-size:.8rem; color:var(--muted); white-space:pre-wrap; margin:12px 0 0; }
  .err { color:#d92d20; }
</style>
</head>
<body>
<main>
  <h1>Google Maps Scraper</h1>
  <p class="sub">Search for businesses, then download the results for Excel or Google Sheets.</p>

  <div class="card">
    <form id="form">
      <label>What to search for (include the place)
        <input id="query" placeholder="restaurants in Ikeja Lagos" required>
      </label>
      <label>How many
        <input id="total" type="number" min="1" max="100" value="20">
      </label>
      <button id="go" type="submit">Start scraping</button>
    </form>
    <p class="sub" style="margin-top:10px">A Chrome window will open on the computer running this app. Leave it alone until it finishes.</p>
  </div>

  <div class="card hidden" id="resultCard">
    <p id="msg"></p>
    <div class="bar"><div id="fill"></div></div>
    <button id="stop" class="secondary hidden" type="button" style="margin-top:12px">Stop</button>
    <pre id="log" class="hidden"></pre>
    <div class="actions hidden" id="actions" style="margin-top:14px">
      <a class="btn" id="csv" href="#">Download CSV</a>
      <a class="btn secondary" id="xlsx" href="#">Download Excel (.xlsx)</a>
    </div>
    <div class="tablewrap"><table id="table"></table></div>
  </div>
</main>

<script>
const form = document.getElementById('form');
const go = document.getElementById('go');
const card = document.getElementById('resultCard');
const msg = document.getElementById('msg');
const fill = document.getElementById('fill');
const actions = document.getElementById('actions');
const table = document.getElementById('table');
let timer = null;
let currentJob = null;
const stopBtn = document.getElementById('stop');
const logBox = document.getElementById('log');

function showLog(lines) {
  if (lines && lines.length) { logBox.textContent = lines.join('\\n'); logBox.classList.remove('hidden'); }
  else { logBox.classList.add('hidden'); }
}

function render(columns, rows) {
  table.replaceChildren();
  if (!rows.length) return;
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  columns.forEach(c => { const th = document.createElement('th'); th.textContent = c; hr.appendChild(th); });
  thead.appendChild(hr);
  const tbody = document.createElement('tbody');
  rows.forEach(r => {
    const tr = document.createElement('tr');
    columns.forEach(c => {
      const td = document.createElement('td');
      const v = r[c];
      td.textContent = (v === null || v === undefined) ? '' : v;
      td.title = td.textContent;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.append(thead, tbody);
}

async function poll(jobId) {
  const res = await fetch('/status/' + jobId);
  const s = await res.json();
  if (s.status === 'running') {
    stopBtn.classList.remove('hidden');
    showLog(s.log);
    const pct = s.count ? Math.round((s.done / s.count) * 100) : 5;
    fill.style.width = pct + '%';
    msg.className = '';
    msg.textContent = s.count
      ? `Scraping "${s.query}": ${s.done} of ${s.count} places...`
      : `Searching Google Maps for "${s.query}"...`;
    return;
  }
  clearInterval(timer);
  go.disabled = false;
  stopBtn.classList.add('hidden');
  if (s.status === 'cancelled') {
    msg.className = '';
    msg.textContent = 'Stopped.';
    fill.style.width = '0%';
    return;
  }
  if (s.status === 'error') {
    msg.className = 'err';
    msg.textContent = 'Something went wrong: ' + s.error;
    showLog(s.log);
    return;
  }
  fill.style.width = '100%';
  msg.className = '';
  msg.textContent = `Done: ${s.rows.length} places found for "${s.query}".`;
  if (s.rows.length) {
    document.getElementById('csv').href = '/download/' + jobId + '.csv';
    document.getElementById('xlsx').href = '/download/' + jobId + '.xlsx';
    actions.classList.remove('hidden');
    render(s.columns, s.rows);
  } else {
    msg.textContent += ' Try a different search.';
    showLog(s.log);
  }
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  go.disabled = true;
  card.classList.remove('hidden');
  actions.classList.add('hidden');
  table.replaceChildren();
  logBox.classList.add('hidden');
  fill.style.width = '3%';
  msg.className = '';
  msg.textContent = 'Starting...';
  const res = await fetch('/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      query: document.getElementById('query').value,
      total: document.getElementById('total').value
    })
  });
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 409 && await resumeRunning()) return;
    msg.className = 'err';
    msg.textContent = data.error || 'Could not start.';
    go.disabled = false;
    return;
  }
  currentJob = data.job_id;
  timer = setInterval(() => poll(data.job_id), 2000);
});

async function resumeRunning() {
  try {
    const r = await fetch('/current');
    const c = await r.json();
    if (!c.job_id) return false;
    currentJob = c.job_id;
    go.disabled = true;
    card.classList.remove('hidden');
    msg.className = '';
    msg.textContent = 'A search is already running. Showing its progress...';
    clearInterval(timer);
    timer = setInterval(() => poll(c.job_id), 2000);
    poll(c.job_id);
    return true;
  } catch (e) { return false; }
}
resumeRunning();

stopBtn.addEventListener('click', async () => {
  if (currentJob) { await fetch('/cancel/' + currentJob, {method: 'POST'}); }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    if PASSWORD:
        print(f"Login is ON. Username: {USERNAME}  (from {LOGIN_SOURCE})", flush=True)
        if host not in ("127.0.0.1", "localhost", "::1") and password_is_weak(PASSWORD):
            print("Refusing to go online with a weak password. Use at least 12 characters "
                  "and avoid common words.", flush=True)
            sys.exit(1)
    else:
        print("WARNING: no login is set.", flush=True)
        if LOGIN_PROBLEM:
            print("  " + LOGIN_PROBLEM, flush=True)
        print(f"  Looking for: {os.path.join(BASE_DIR, 'login.txt')}", flush=True)
        if host not in ("127.0.0.1", "localhost", "::1"):
            print("Refusing to start on a network address without a login.", flush=True)
            sys.exit(1)
        print("  Running without a login is only safe because this copy listens on this PC only.", flush=True)
    app.run(host=host, port=port, debug=False, threaded=True)
