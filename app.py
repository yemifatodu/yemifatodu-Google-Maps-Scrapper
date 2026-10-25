"""
Web interface for the Google Maps scraper.

Run:   python app.py
Open:  http://127.0.0.1:5000

Login (recommended if you ever expose this beyond your own PC):
    PowerShell:  $env:APP_USERNAME = "your-username"
                 $env:APP_PASSWORD = "your-password"
                 python app.py
Optional host/port:
    $env:HOST = "0.0.0.0"; $env:PORT = "5000"
"""
import hmac
import io
import os
import threading
import uuid
from dataclasses import asdict, fields

import pandas as pd
from flask import Flask, Response, jsonify, render_template_string, request, send_file

import maps_scraper as scraper

app = Flask(__name__)
jobs = {}
jobs_lock = threading.Lock()
USERNAME = os.environ.get("APP_USERNAME", "admin")
PASSWORD = os.environ.get("APP_PASSWORD")
COLUMNS = [f.name for f in fields(scraper.Place)]


def same(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


@app.before_request
def require_login():
    if not PASSWORD:
        return None
    auth = request.authorization
    if not auth or not (same(auth.username, USERNAME) and same(auth.password, PASSWORD)):
        return Response(
            "Login required. Enter your username and password.",
            401,
            {"WWW-Authenticate": 'Basic realm="Maps Scraper"'},
        )
    return None


def run_job(job_id: str, query: str, total: int):
    job = jobs[job_id]

    def on_progress(done, count):
        job["done"] = done
        job["count"] = count

    try:
        places = scraper.scrape_places(query, total, on_progress=on_progress)
        job["rows"] = [asdict(p) for p in places]
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)


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
        columns=COLUMNS,
    )


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
  if (s.status === 'error') {
    msg.className = 'err';
    msg.textContent = 'Something went wrong: ' + s.error;
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
  }
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  go.disabled = true;
  card.classList.remove('hidden');
  actions.classList.add('hidden');
  table.replaceChildren();
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
    msg.className = 'err';
    msg.textContent = data.error || 'Could not start.';
    go.disabled = false;
    return;
  }
  timer = setInterval(() => poll(data.job_id), 2000);
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=False, threaded=True)
