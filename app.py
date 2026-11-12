from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote
from pathlib import Path
from content_engine import _normal_words, rank_candidate_excerpts, build_story_parts, detect_narrative_sequences
from annotation_store import RATINGS, REASONS, SOURCE_TYPES, candidate_id, load_annotations, load_reference_spans, load_source_catalog, register_source, save_annotation, save_reference_span, source_fingerprint
from email.utils import parsedate_to_datetime
import json, html, uuid, datetime, threading, webbrowser, re, os, subprocess, time, mimetypes, traceback, difflib, contextvars

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data.json"
STUDIO = ROOT / "studio.json"
SESSIONS_DIR = ROOT / "sessions"
SESSIONS_INDEX = SESSIONS_DIR / "index.json"
ACTIVE_SESSION_ID = contextvars.ContextVar("clip_factory_session_id", default="default")
ACTIVE_SESSION_WORKERS = set()
ACTIVE_SESSION_WORKERS_LOCK = threading.Lock()
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
MEDIA = ROOT / "media"
SOURCES = MEDIA / "sources"
EXPORTS = MEDIA / "exports"
for folder in (SOURCES, EXPORTS):
    folder.mkdir(parents=True, exist_ok=True)
HOST, PORT = "127.0.0.1", 8765
MAX_UPLOAD = 3 * 1024 * 1024 * 1024

DEFAULT = {
    "settings": {"monthly_goal": 500.0, "currency": "EUR"},
    "campaigns": [],
    "clips": []
}

def load_data():
    if not DATA.exists():
        save_data(DEFAULT)
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        d = json.loads(json.dumps(DEFAULT))
    d.setdefault("settings", {"monthly_goal": 500.0, "currency": "EUR"})
    d.setdefault("campaigns", [])
    d.setdefault("clips", [])
    return d

def save_data(data):
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA)

def empty_studio(message="Nouvelle session : charge une vidéo pour commencer."):
    return {"source": None, "duration": 0, "width": 0, "height": 0,
            "status": "Aucune vidéo chargée", "message": message, "transcript": None,
            "last_output": None, "suggestions": [], "scan_full": False,
            "parts": [], "parts_count": 4, "analysis_mode": "highlights", "operation": "transcribe"}

def studio_path(session_id=None):
    sid = str(session_id or ACTIVE_SESSION_ID.get() or "default")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", sid):
        sid = "default"
    return STUDIO if sid == "default" else SESSIONS_DIR / f"session_{sid}.json"

def _write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def load_sessions_index():
    if not SESSIONS_INDEX.exists():
        legacy = {}
        if STUDIO.exists():
            try: legacy = json.loads(STUDIO.read_text(encoding="utf-8"))
            except Exception: legacy = {}
        label = Path(legacy.get("source") or "").name or "Session actuelle"
        _write_json_atomic(SESSIONS_INDEX, {"sessions": [{"id": "default", "label": label, "open": True}]})
    try:
        data = json.loads(SESSIONS_INDEX.read_text(encoding="utf-8"))
        if not isinstance(data.get("sessions"), list): raise ValueError("index invalide")
        if not any(x.get("id") == "default" for x in data["sessions"]):
            data["sessions"].insert(0, {"id": "default", "label": "Session actuelle", "open": True})
        return data
    except Exception:
        return {"sessions": [{"id": "default", "label": "Session actuelle", "open": True}]}

def save_sessions_index(data):
    _write_json_atomic(SESSIONS_INDEX, data)

def update_session_label(session_id, label):
    data = load_sessions_index()
    for item in data["sessions"]:
        if item.get("id") == session_id:
            item["label"] = (str(label or "").strip()[:48] or "Session vidéo")
            item["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            break
    save_sessions_index(data)

def create_session():
    data = load_sessions_index()
    sid = uuid.uuid4().hex[:8]
    data["sessions"].append({"id": sid, "label": "Nouvelle vidéo", "open": True,
                             "updated": datetime.datetime.now().isoformat(timespec="seconds")})
    save_sessions_index(data)
    _write_json_atomic(studio_path(sid), empty_studio())
    return sid

def close_session_tab(session_id):
    data = load_sessions_index()
    for item in data["sessions"]:
        if item.get("id") == session_id:
            item["open"] = False
            break
    save_sessions_index(data)
    return data

def open_session_tab(session_id):
    data = load_sessions_index()
    for item in data["sessions"]:
        if item.get("id") == session_id:
            item["open"] = True
            item["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            save_sessions_index(data)
            return True
    return False

def studio_tabs_html(active_id):
    data = load_sessions_index()
    opened = [x for x in data["sessions"] if x.get("open")]
    closed = [x for x in data["sessions"] if not x.get("open")]
    tabs = []
    for item in opened:
        sid = item["id"]
        label = esc(item.get("label") or "Session vidéo")
        active = " active" if sid == active_id else ""
        close_link = f"/studio?sid={sid}&close={sid}" if len(opened) > 1 else "#"
        close_attr = "" if len(opened) > 1 else " title='Garde au moins un onglet ouvert' aria-disabled='true'"
        tabs.append(f"<div class='session-tab{active}'><a class='session-tab-label' href='/studio?sid={sid}'>{label}</a><a class='session-tab-close' href='{close_link}'{close_attr} aria-label='Fermer {label}'>×</a></div>")
    tabs.append("<a class='session-tab-new' href='/studio?new=1' title='Nouvelle session'>＋</a>")
    closed_html = ""
    if closed:
        items = " · ".join(f"<a href='/studio?sid={x['id']}&reopen=1'>{esc(x.get('label') or 'Session vidéo')}</a>" for x in closed)
        closed_html = f"<details class='closed-sessions'><summary>Sessions fermées ({len(closed)})</summary><div>{items}</div></details>"
    return "<div class='session-tabs'>" + "".join(tabs) + "</div>" + closed_html

def load_studio():
    path = studio_path()
    if not path.exists():
        _write_json_atomic(path, empty_studio())
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_studio("Le fichier de session était illisible ; une session vide a été ouverte.")

def save_studio(s):
    _write_json_atomic(studio_path(), s)

def save_transcript_snapshot(transcript):
    sid = ACTIVE_SESSION_ID.get()
    path = ROOT / "transcript.json" if sid == "default" else SESSIONS_DIR / f"transcript_{sid}.json"
    _write_json_atomic(path, transcript)

def spawn_session_worker(target, args=()):
    """Start a worker while copying the current tab/session context into its thread."""
    ctx = contextvars.copy_context()
    sid = str(ACTIVE_SESSION_ID.get() or "default")
    with ACTIVE_SESSION_WORKERS_LOCK:
        if sid in ACTIVE_SESSION_WORKERS:
            raise RuntimeError("Un traitement est déjà actif dans cette session.")
        ACTIVE_SESSION_WORKERS.add(sid)

    def run_worker():
        try:
            ctx.run(target, *args)
        finally:
            with ACTIVE_SESSION_WORKERS_LOCK:
                ACTIVE_SESSION_WORKERS.discard(sid)

    thread = threading.Thread(target=run_worker, name=f"clip-factory-{sid}", daemon=True)
    try:
        thread.start()
    except Exception:
        with ACTIVE_SESSION_WORKERS_LOCK:
            ACTIVE_SESSION_WORKERS.discard(sid)
        raise
    return thread


def session_worker_active(session_id=None):
    sid = str(session_id or ACTIVE_SESSION_ID.get() or "default")
    with ACTIVE_SESSION_WORKERS_LOCK:
        return sid in ACTIVE_SESSION_WORKERS

def esc(x):
    return html.escape(str(x or ""), quote=True)


def parse_user_float(value, field_name="valeur"):
    """Parse seconds/prices accepting both French decimal commas and dots."""
    raw = str(value or "").strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name.capitalize()} invalide : {value!r}. Utilise un nombre, par exemple 12,5.")


def default_transcription_prompt(source_name=""):
    return ""


def suggested_clip_title(studio=None, data=None):
    """Suggest the next unused numbered clip title, considering exports and the dashboard."""
    studio = studio or load_studio()
    data = data or load_data()
    source_name = Path(studio.get("source") or "").stem.lower()
    prefix = "Clip"
    used = []
    pattern = re.compile(r"(?:clip)[ _-]*0*(\d+)", re.IGNORECASE)
    for clip in data.get("clips", []):
        title = str(clip.get("title", ""))
        if prefix.lower() in title.lower() or prefix == "Clip":
            used.extend(int(m.group(1)) for m in pattern.finditer(title))
        local_file = str(clip.get("local_file", ""))
        if local_file:
            used.extend(int(m.group(1)) for m in pattern.finditer(Path(local_file).stem))
    if EXPORTS.exists():
        for path in EXPORTS.glob("*.mp4"):
            stem = path.stem
            if prefix.lower() in stem.lower() or prefix == "Clip":
                used.extend(int(m.group(1)) for m in pattern.finditer(stem))
    number = max(used, default=0) + 1
    return f"{prefix} – clip {number:02d}"


def unique_clip_title(title, studio=None, data=None):
    data = data or load_data()
    existing = {str(c.get("title", "")).strip().casefold() for c in data.get("clips", [])}
    candidate = str(title or "").strip()
    if not candidate or candidate.casefold() in existing:
        candidate = suggested_clip_title(studio, data)
    # If a manually entered title is already used, make the new one distinct.
    if candidate.casefold() in existing:
        base = candidate
        n = 2
        while f"{base} ({n})".casefold() in existing:
            n += 1
        candidate = f"{base} ({n})"
    return candidate

def page(body, title="Clip Factory", refresh=False, session_id="default"):
    script = r"""
<script>
(() => {
  const sessionScope = document.body.dataset.sessionId || 'default';
  const scrollKey = 'clipFactoryReturnScroll:' + sessionScope;
  const pathKey = 'clipFactoryReturnPath:' + sessionScope;
  const themeKey = 'clipFactoryTheme';
  const applyTheme = (theme) => { document.documentElement.dataset.theme = theme; try { localStorage.setItem(themeKey, theme); } catch (_) {} };
  try { applyTheme(localStorage.getItem(themeKey) || 'dark'); } catch (_) { applyTheme('dark'); }
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (form && (form.method || 'get').toLowerCase() === 'post') {
      try {
        sessionStorage.setItem(scrollKey, String(window.scrollY));
        sessionStorage.setItem(pathKey, window.location.pathname);
      } catch (_) {}
    }
  }, true);
  window.addEventListener('DOMContentLoaded', () => {
    try {
      const savedPath = sessionStorage.getItem(pathKey);
      const savedY = sessionStorage.getItem(scrollKey);
      if (savedY !== null && savedPath === window.location.pathname) {
        sessionStorage.removeItem(scrollKey);
        sessionStorage.removeItem(pathKey);
        const y = Number(savedY) || 0;
        requestAnimationFrame(() => requestAnimationFrame(() => window.scrollTo(0, y)));
        setTimeout(() => window.scrollTo(0, y), 250);
      }
    } catch (_) {}

    // Accept French decimal commas in all seconds fields before POST.
    // Decimal inputs are text fields so French commas remain visible; the server parses either comma or dot.

    // Candidate preview: one seekable player, limited to the candidate interval.
    const previewVideo = document.getElementById('candidate-preview-video');
    const previewPanel = document.getElementById('candidate-preview-panel');
    const previewLabel = document.getElementById('candidate-preview-label');
    let previewStopAt = null;
    if (previewVideo) {
      previewVideo.addEventListener('timeupdate', () => {
        if (previewStopAt !== null && previewVideo.currentTime >= previewStopAt) {
          previewVideo.pause();
          previewVideo.currentTime = previewStopAt;
          previewStopAt = null;
        }
      });
    }
    document.querySelectorAll('[data-preview-start]').forEach((button) => {
      button.addEventListener('click', () => {
        if (!previewVideo) return;
        const a = Number(button.dataset.previewStart), b = Number(button.dataset.previewEnd);
        previewStopAt = b;
        if (previewPanel) previewPanel.hidden = false;
        if (previewLabel) previewLabel.textContent = 'Prévisualisation du passage ' + a.toFixed(2) + ' s → ' + b.toFixed(2) + ' s. Le lecteur s’arrêtera à la fin du passage.';
        const seekAndPlay = () => {
          try { previewVideo.currentTime = a; } catch (_) {}
          const p = previewVideo.play(); if (p && p.catch) p.catch(() => {});
        };
        if (previewVideo.readyState >= 1) seekAndPlay();
        else previewVideo.addEventListener('loadedmetadata', seekAndPlay, {once:true});
        if (previewPanel) previewPanel.scrollIntoView({behavior:'smooth', block:'center'});
      });
    });

    const candidateSort = document.getElementById('candidate-sort');
    if (candidateSort) candidateSort.addEventListener('change', () => {
      const list = document.getElementById('candidate-list');
      if (!list) return;
      const cards = Array.from(list.querySelectorAll('.candidate[data-score]'));
      const key = candidateSort.value;
      cards.sort((a, b) => Number(b.dataset[key] || 0) - Number(a.dataset[key] || 0));
      cards.forEach((card) => list.appendChild(card));
    });

    // Choosing a candidate fills the exact start/end (French comma display) and hides the list.
    document.querySelectorAll('[data-candidate-start]').forEach((button) => {
      button.addEventListener('click', () => {
        const start = document.getElementById('transcribe-start');
        const end = document.getElementById('transcribe-end');
        const scanForm = document.getElementById('transcribe-form');
        const a = Number(button.dataset.candidateStart), b = Number(button.dataset.candidateEnd);
        const fmt = (n) => n.toLocaleString('fr-FR', {minimumFractionDigits:2, maximumFractionDigits:2});
        if (start) start.value = fmt(a);
        if (end) end.value = fmt(b);
        const box = document.getElementById('candidate-results');
        const summary = document.getElementById('candidate-selected-summary');
        const summaryText = document.getElementById('candidate-selected-text');
        if (box) box.hidden = true;
        if (summary) summary.hidden = false;
        if (summaryText) summaryText.textContent = 'Extrait choisi : ' + fmt(a) + ' s → ' + fmt(b) + ' s (' + (b-a).toFixed(1) + ' s). Tu peux maintenant lancer la transcription.';
        if (scanForm) scanForm.scrollIntoView({behavior:'smooth', block:'center'});
      });
    });
    const changeCandidate = document.getElementById('candidate-change');
    if (changeCandidate) changeCandidate.addEventListener('click', () => {
      const box = document.getElementById('candidate-results');
      const summary = document.getElementById('candidate-selected-summary');
      if (box) box.hidden = false;
      if (summary) summary.hidden = true;
      if (box) box.scrollIntoView({behavior:'smooth', block:'start'});
    });

    const themeButton = document.getElementById('theme-toggle');
    const refreshThemeLabel = () => { if (themeButton) themeButton.textContent = document.documentElement.dataset.theme === 'light' ? '☾ Mode sombre' : '☀ Mode clair'; };
    refreshThemeLabel();
    if (themeButton) themeButton.addEventListener('click', () => { applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'); refreshThemeLabel(); });
    const studioPage = document.body.dataset.studio === '1';
    if (!studioPage) return;
    let wasBusy = document.body.dataset.busy === '1';
    async function pollStatus() {
      try {
        const sessionId = document.body.dataset.sessionId || 'default';
        const response = await fetch('/status?sid=' + encodeURIComponent(sessionId), {cache:'no-store'});
        if (!response.ok) return;
        const data = await response.json();
        let operation = data.operation || 'transcribe';
        if (operation === 'candidates' || operation === 'parts') operation = 'analysis';
        const status = document.getElementById(operation + '-status-value');
        const message = document.getElementById(operation + '-status-message');
        if (status) status.textContent = data.status || '';
        if (message) message.textContent = data.message || '';
        if (wasBusy && !data.busy) {
          try { sessionStorage.setItem(scrollKey, String(window.scrollY)); sessionStorage.setItem(pathKey, window.location.pathname); } catch (_) {}
          window.location.reload();
          return;
        }
        wasBusy = !!data.busy;
      } catch (_) {}
    }
    setInterval(pollStatus, 1800);
  });
})();
</script>
"""
    body_attr = (f' data-studio="1" data-session-id="{html.escape(str(session_id), quote=True)}"') if 'Atelier' in title else ''
    studio_nav_href = f"/studio?sid={session_id}" if "Atelier" in title else "/studio?new=1"
    rendered = f"""<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title><style>
:root{{--bg:#0b1020;--panel:#151d32;--line:#293550;--muted:#9eacc6;--text:#f3f6ff;--accent:#a3b0ff;--green:#54d6a0;--red:#ff7d89;--yellow:#ffd36a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Segoe UI,Arial,sans-serif}}
header{{padding:20px max(20px,calc((100vw - 1120px)/2));border-bottom:1px solid var(--line);background:#10182b}}
header h1{{margin:0;font-size:25px}}header p{{margin:4px 0 0;color:var(--muted)}}nav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}}nav a{{color:#dfe5ff;text-decoration:none;background:#202b45;padding:7px 12px;border-radius:8px;font-size:13px}}nav a.active{{background:var(--accent);color:#10162a;font-weight:700}}
main{{max-width:1120px;margin:24px auto;padding:0 18px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px;scroll-margin-top:18px}}
.kpi small,.muted{{color:var(--muted)}}.kpi strong{{display:block;font-size:27px;margin-top:4px}}
h2{{font-size:18px;margin:0 0 12px}}h3{{font-size:16px;margin:0 0 8px}}.cols{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
label{{display:block;font-size:13px;color:var(--muted);margin:8px 0 4px}}input,select,textarea{{width:100%;padding:10px;border-radius:8px;border:1px solid var(--line);background:#0e1628;color:var(--text)}}textarea{{min-height:72px;resize:vertical}}
button,.btn{{display:inline-block;border:0;border-radius:8px;background:var(--accent);color:#10162a;padding:10px 14px;font-weight:700;cursor:pointer;text-decoration:none;margin-top:10px}}button.secondary,.btn.secondary{{background:#293550;color:var(--text)}}button:disabled{{opacity:.55;cursor:not-allowed}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:10px 8px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-weight:600}}
.pill{{display:inline-block;padding:3px 8px;border-radius:20px;background:#273451;color:#cbd5ff;font-size:12px}}.good{{color:var(--green)}}.warn{{color:var(--yellow)}}.bad{{color:var(--red)}}
.note{{padding:12px;border-radius:10px;background:#202b45;color:#d5def3;font-size:13px}}.status{{padding:12px;background:#0e1628;border:1px solid var(--line);border-radius:10px;margin:10px 0}}
.transcript{{max-height:620px;overflow:auto}}.line{{padding:9px 10px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:145px 1fr;gap:12px}}.time{{color:var(--accent);font-variant-numeric:tabular-nums;font-size:13px}}.edit-line{{padding:10px 0;border-bottom:1px solid var(--line);display:grid;grid-template-columns:145px 1fr;gap:12px;align-items:start}}.edit-line textarea{{min-height:54px;font:14px/1.45 Segoe UI,Arial,sans-serif}}.edit-help{{font-size:13px;color:var(--muted);margin:8px 0 12px}}
.candidate{{border:1px solid var(--line);background:#10182b;border-radius:10px;padding:12px;margin:10px 0}}
.session-tabs{{display:flex;align-items:stretch;gap:6px;flex-wrap:wrap;margin:0 0 16px;padding:8px;background:var(--panel);border:1px solid var(--line);border-radius:12px}}
.session-tab{{display:flex;align-items:center;gap:2px;max-width:260px;background:#202b45;border:1px solid #34415e;border-radius:8px;color:var(--text)}}
.session-tab.active{{background:#2b3858;border-color:var(--accent);box-shadow:inset 0 -2px 0 var(--accent)}}
.session-tab-label{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:8px 10px;color:inherit;text-decoration:none;font-size:13px}}
.session-tab-close{{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;margin:0 4px;border-radius:6px;text-decoration:none;color:inherit;font-size:18px}}
.session-tab-close:hover{{background:rgba(255,100,100,.18)}}.session-tab-close[aria-disabled="true"]{{opacity:.3;pointer-events:none}}
.session-tab-new{{display:inline-flex;align-items:center;justify-content:center;min-width:38px;border:1px dashed var(--muted);border-radius:8px;color:var(--text);text-decoration:none;font-size:21px}}
.closed-sessions{{margin:-6px 0 14px;color:var(--muted);font-size:12px}}.closed-sessions div{{padding:8px 0}}.closed-sessions a{{color:var(--accent);margin-right:12px}}
.upload-hint{{font-size:13px;color:var(--muted);margin-top:7px}}
.candidate-head{{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}}.score{{font-size:20px;font-weight:800;color:var(--green)}}.candidate-quote{{font-size:14px;line-height:1.55;margin:8px 0;color:#edf1ff}}.candidate-reasons{{color:var(--muted);font-size:12px}}.candidate-preview{{width:100%;max-height:460px;background:#05080f;border-radius:10px;margin:10px 0}}.candidate-actions{{display:flex;gap:8px;flex-wrap:wrap}}.candidate-selected{{border:1px solid var(--green);background:rgba(84,214,160,.08);border-radius:10px;padding:12px;margin:10px 0}}.confidence-note{{font-size:12px;color:var(--yellow);margin-top:5px}}[hidden]{{display:none!important}}
progress{{width:100%;height:12px}}footer{{color:var(--muted);font-size:12px;margin:24px 0}}
@media(max-width:760px){{.grid{{grid-template-columns:repeat(2,1fr)}}.cols{{grid-template-columns:1fr}}table{{display:block;overflow-x:auto}}.line,.edit-line{{grid-template-columns:1fr;gap:2px}}}}
html[data-theme="light"]{{--bg:#f3f5fa;--panel:#ffffff;--line:#d8deea;--muted:#596579;--text:#172033;--accent:#5968d9;--green:#087f5b;--red:#c92a3a;--yellow:#9a6700}}
html[data-theme="light"] header{{background:#ffffff}}html[data-theme="light"] nav a{{background:#e8ecf5;color:#25304a}}html[data-theme="light"] nav a.active{{background:var(--accent);color:#fff}}
html[data-theme="light"] input,html[data-theme="light"] select,html[data-theme="light"] textarea{{background:#f8f9fc;color:#172033}}
html[data-theme="light"] .note{{background:#eef2fb;color:#29354c}}html[data-theme="light"] .status{{background:#f8f9fc}}html[data-theme="light"] .candidate{{background:#f8f9fc}}html[data-theme="light"] .candidate-quote{{color:#25304a}}html[data-theme="light"] .candidate-selected{{background:#effaf5}}html[data-theme="light"] button.secondary,html[data-theme="light"] .btn.secondary{{background:#e4e8f1;color:#25304a}}
html[data-theme="light"] button:not(.secondary):not(.theme-toggle),html[data-theme="light"] .btn:not(.secondary){{background:#3548b8;color:#ffffff;border:1px solid #2d3f9e;box-shadow:0 1px 2px rgba(20,35,90,.12)}}
html[data-theme="light"] button:not(.secondary):not(.theme-toggle):hover,html[data-theme="light"] .btn:not(.secondary):hover{{background:#293b9f;color:#ffffff}}
html[data-theme="light"] button.secondary,html[data-theme="light"] .btn.secondary{{background:#e9edf6;color:#1e293b;border:1px solid #cbd4e4}}
html[data-theme="light"] button.secondary:hover,html[data-theme="light"] .btn.secondary:hover{{background:#dce4f2;color:#111827}}
html[data-theme="light"] .session-tab{{background:#e9edf6;border-color:#cbd4e4}}
html[data-theme="light"] .session-tab.active{{background:#ffffff;border-color:#5869d8}}

.theme-toggle{{float:right;margin:0 0 8px 12px;background:transparent;border:1px solid var(--line);color:var(--text);padding:7px 10px}}
.operation-status{{margin-top:12px}}.emphasis-help{{font-size:12px;color:var(--muted)}}
</style></head><body{body_attr} data-busy=\"{'1' if refresh else '0'}\"><header><button type="button" id="theme-toggle" class="theme-toggle">☀ Mode clair</button><h1>🎬 Clip Factory</h1><p>Studio local de transcription et de production vidéo</p><nav><a href=\"/\" class=\"{'active' if title.endswith('Tableau de bord') else ''}\">Tableau de bord</a><a href=\"{studio_nav_href}\" class=\"{'active' if 'Atelier' in title else ''}\">Atelier vidéo</a></nav></header><main>{body}<footer>Traitement local sur cet ordinateur. Les vidéos et transcriptions ne sont envoyées à aucun service par Clip Factory. L'analyse de passages repose sur des indices de transcription, pas sur une garantie de viralité.</footer></main>{script}</body></html>"""
    nav_links = "<a href='/#campaigns'>Campagnes</a><a href='/library'>Bibliothèque</a><a href='/#clips'>Clips & exports</a><a href='/#analytics'>Statistiques</a><a href='/#settings'>Paramètres</a>"
    rendered = rendered.replace("</nav>", nav_links + "</nav>")
    return rendered

def money(v):
    return f"{float(v or 0):,.2f} €".replace(",", " ").replace(".", ",")

def dashboard():
    d = load_data()
    clips = d["clips"]
    approved = [c for c in clips if c.get("status") == "Approuvé"]
    views = sum(int(c.get("eligible_views", 0) or 0) for c in approved)
    earnings = sum(float(c.get("earnings", 0) or 0) for c in approved)
    costs = sum(float(c.get("cost", 0) or 0) for c in clips)
    pending = sum(1 for c in clips if c.get("status") in ("À publier", "Publié – à soumettre", "Soumis – en attente"))
    goal = float(d["settings"].get("monthly_goal", 500))
    pct = min(100, earnings / goal * 100) if goal else 0
    campaign_rows = "".join(f"<tr><td><b>{esc(c['name'])}</b><br><span class='muted'>{esc(c.get('platform', ''))}</span></td><td>{esc(c.get('niche', ''))}</td><td>{esc(c.get('deadline', '—'))}</td><td>{money(c.get('rate', 0))}</td><td>{esc(c.get('status', 'Active'))}</td></tr>" for c in d["campaigns"])
    clip_rows = "".join(f"<tr><td>{esc(c['title'])}<br><span class='muted'>{esc(c.get('account', ''))}</span></td><td>{esc(c.get('campaign', '—'))}</td><td>{esc(c.get('status', ''))}</td><td>{int(c.get('eligible_views', 0) or 0):,}</td><td>{money(c.get('earnings', 0))}</td></tr>" for c in clips[-10:][::-1]) or "<tr><td colspan='5' class='muted'>Aucun clip enregistré.</td></tr>"
    studio = load_studio()
    source_name = Path(studio["source"]).name if studio.get("source") else "Aucune vidéo"
    body = f"""
<div class="grid">
 <div class="card kpi"><small>Gains validés</small><strong>{money(earnings)}</strong></div><div class="card kpi"><small>Vues éligibles</small><strong>{views:,}</strong></div><div class="card kpi"><small>Clips en cours</small><strong>{pending}</strong></div><div class="card kpi"><small>Coûts enregistrés</small><strong>{money(costs)}</strong></div>
</div>
<div class="card"><div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap"><h2>Objectif mensuel : {money(goal)}</h2><b>{pct:.1f} % atteint</b></div><div style="height:12px;background:#0b1020;border-radius:10px;overflow:hidden"><div style="height:100%;width:{pct}%;background:var(--green)"></div></div><p class="muted">Net indicatif après coûts enregistrés : <b>{money(earnings-costs)}</b>.</p></div>
<div class="card"><h2>Atelier vidéo</h2><p>Vidéo actuelle : <b>{esc(source_name)}</b></p><p class="muted">Transcription : {esc(studio.get('status', 'Aucune vidéo chargée'))}</p><a class="btn" href="/studio?new=1">Ouvrir un nouvel atelier vidéo →</a> <a class="btn secondary" href="/library">Ouvrir la bibliothèque →</a></div>
<div class="cols">
<section class="card"><h2>Ajouter une campagne</h2><form method="post" action="/campaign"><label>Nom de la campagne *</label><input name="name" required placeholder="Nom de campagne"><label>Plateforme</label><select name="platform"><option>Clippeurs.com</option><option>Klipeak</option><option>MetaClip</option><option>Autre</option></select><label>Niche / compte</label><select name="niche"><option>Clipping / créateurs</option><option>Gaming & applications</option><option>Autre</option></select><label>Date limite</label><input type="date" name="deadline"><div class="cols"><div><label>Durée minimale d'un clip (s)</label><input type="number" min="3" max="300" name="min_duration" value="20"></div><div><label>Durée maximale d'un clip (s)</label><input type="number" min="3" max="300" name="max_duration" value="45"></div></div><label>Rémunération pour 1 000 vues (€)</label><input type="number" min="0" step="0.01" name="rate" value="0"><label>Budget restant (€)</label><input type="number" min="0" step="0.01" name="budget"><label>Consignes</label><textarea name="rules"></textarea><button>Enregistrer</button></form></section>
<section class="card"><h2>Ajouter un clip manuellement</h2><form method="post" action="/clip"><label>Titre interne *</label><input name="title" required><label>Compte</label><select name="account"><option>Compte A – cut.on.l0op</option><option>Compte B – Gaming & Apps</option></select><label>Campagne</label><select name="campaign">{''.join(f'<option>{esc(c["name"])}</option>' for c in d['campaigns']) or '<option>À définir</option>'}</select><label>Statut</label><select name="status"><option>À publier</option><option>Publié – à soumettre</option><option>Soumis – en attente</option><option>Approuvé</option><option>Refusé</option></select><label>Lien TikTok</label><input name="url" type="url"><label>Vues éligibles validées</label><input name="views" type="number" min="0" value="0"><label>Gains validés (€)</label><input name="earnings" type="number" min="0" step="0.01" value="0"><label>Coût (€)</label><input name="cost" type="number" min="0" step="0.01" value="0"><button>Enregistrer</button></form></section>
</div>
<div class="card"><h2>Campagnes enregistrées</h2><table><thead><tr><th>Campagne</th><th>Niche</th><th>Fin</th><th>€/1 000 vues</th><th>Statut</th></tr></thead><tbody>{campaign_rows or '<tr><td colspan="5">Aucune campagne</td></tr>'}</tbody></table></div>
<div class="card"><h2>Derniers clips</h2><table><thead><tr><th>Clip / compte</th><th>Campagne</th><th>Statut</th><th>Vues</th><th>Gains</th></tr></thead><tbody>{clip_rows}</tbody></table></div>
<div class="card"><h2>Paramètres</h2><form method="post" action="/settings"><label>Objectif mensuel (€)</label><input name="goal" type="number" min="0" step="1" value="{goal}"><button class="secondary">Mettre à jour</button></form><a class="btn secondary" href="/export">Exporter JSON</a> <a class="btn secondary" href="/csv">Exporter CSV</a></div>
<div class="note"><b>Rappel :</b> Clip Factory prépare les clips. Vérifie toujours que chaque campagne autorise bien la réutilisation et le montage de la source avant publication.</div>
"""
    body = body.replace('<label>Date limite</label><input type="date" name="deadline">', '<label>Date limite</label><input type="date" name="deadline"><div class="cols"><div><label>Durée minimale d\'un clip (s)</label><input type="number" min="3" max="300" name="min_duration" value="20"></div><div><label>Durée maximale d\'un clip (s)</label><input type="number" min="3" max="300" name="max_duration" value="45"></div></div>')
    body = body.replace('<h2>Campagnes enregistrées</h2>', '<h2 id="campaigns">Campagnes enregistrées</h2>')
    body = body.replace('<h2>Derniers clips</h2>', '<h2 id="clips">Derniers clips</h2>')
    body = body.replace('<h2>Paramètres</h2>', '<h2 id="settings">Paramètres</h2>')
    body = body.replace('<h2>Objectif mensuel :', '<h2 id="analytics">Objectif mensuel :')
    return page(body, "Clip Factory — Tableau de bord")


def library_page():
    """List local sources, exports and saved sessions without moving or deleting files."""
    def file_row(path, kind):
        size_mb = path.stat().st_size / (1024 * 1024)
        updated = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
        download = f"<a href='/files/{esc(path.name)}' download>Télécharger</a>" if kind == "export" else "Source locale"
        return f"<tr><td>{esc(path.name)}</td><td>{size_mb:.1f} Mo</td><td>{updated}</td><td>{download}</td></tr>"

    sources = sorted((p for p in SOURCES.glob("*") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    exports = sorted((p for p in EXPORTS.glob("*") if p.is_file() and p.suffix.lower() == ".mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    sessions = load_sessions_index().get("sessions", [])
    session_rows = "".join(
        f"<tr><td>{esc(item.get('label','Session vidéo'))}</td><td>{'Ouverte' if item.get('open') else 'Fermée'}</td><td><a href='/studio?sid={esc(item.get('id','default'))}'>Ouvrir</a></td></tr>"
        for item in sessions
    ) or "<tr><td colspan='3'>Aucune session enregistrée.</td></tr>"
    body = (
        "<div class='card'><h2>Vidéos sources</h2><p class='muted'>Fichiers conservés localement dans la bibliothèque. Ils ne sont pas déplacés ni nettoyés automatiquement.</p>"
        + ("<table><thead><tr><th>Fichier</th><th>Taille</th><th>Modifié</th><th>Usage</th></tr></thead><tbody>" + "".join(file_row(p, "source") for p in sources) + "</tbody></table>" if sources else "<p>Aucune source importée.</p>")
        + "</div><div class='card'><h2>Exports MP4</h2>"
        + ("<table><thead><tr><th>Fichier</th><th>Taille</th><th>Modifié</th><th>Action</th></tr></thead><tbody>" + "".join(file_row(p, "export") for p in exports) + "</tbody></table>" if exports else "<p>Aucun export disponible.</p>")
        + "</div><div class='card'><h2>Sessions vidéo</h2><table><thead><tr><th>Session</th><th>État</th><th>Action</th></tr></thead><tbody>" + session_rows + "</tbody></table></div>"
    )
    return page(body, "Clip Factory — Bibliothèque")

def fmt_time(seconds):
    seconds = max(0.0, float(seconds or 0))
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:05.2f}"






def detect_framing(source, start, end):
    """Choose crop only when the visible action is safely inside the central 9:16 crop.
    OpenCV Haar face detection is optional; the fallback is deliberately conservative.
    """
    width, height = get_video_size(source)
    ratio = (width / height) if width and height else 0
    if not ratio:
        return "blur", "dimensions non détectées : image entière conservée par sécurité"
    target_ratio = 9/16
    if abs(ratio-target_ratio) < 0.035:
        return "crop", "source déjà proche du 9:16"
    try:
        import cv2
        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            raise RuntimeError("OpenCV ne peut pas lire la source")
        cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            raise RuntimeError("classificateur de visages indisponible")
        sample_count = 7
        sample_times = [start + (end-start)*(k+0.5)/sample_count for k in range(sample_count)]
        total_faces = 0
        contained_faces = 0
        face_frames = 0
        centres=[]
        edge_scores=[]
        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0,t)*1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h0,w0=frame.shape[:2]
            scale=min(640/max(1,w0), 360/max(1,h0), 1.0)
            if scale<1:
                frame=cv2.resize(frame,(int(w0*scale),int(h0*scale)),interpolation=cv2.INTER_AREA)
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            gray=cv2.equalizeHist(gray)
            faces=cascade.detectMultiScale(gray,scaleFactor=1.10,minNeighbors=5,minSize=(22,22))
            if len(faces):
                face_frames += 1
            frame_w=gray.shape[1]
            crop_w=gray.shape[0]*target_ratio
            crop_x0=(frame_w-crop_w)/2
            crop_x1=crop_x0+crop_w
            for (x,y,w,h) in faces:
                total_faces += 1
                cx=x+w/2
                centres.append(cx/frame_w)
                pad=w*0.10
                if x >= crop_x0-pad and (x+w) <= crop_x1+pad:
                    contained_faces += 1
            # Fallback signal for screen/animation content: compare edge density in central crop.
            edges=cv2.Canny(gray,65,150)
            if edges.sum()>0:
                x0=max(0,int(crop_x0)); x1=min(frame_w,int(crop_x1))
                edge_scores.append(float(edges[:,x0:x1].sum())/float(edges.sum()))
        cap.release()
        if total_faces >= 2 and face_frames >= 3:
            coverage=contained_faces/total_faces
            if coverage >= 0.88:
                center=sum(centres)/len(centres) if centres else 0.5
                return "crop", f"visages détectés au centre ({contained_faces}/{total_faces} dans le cadrage vertical, sur {face_frames} images)"
            return "blur", f"plusieurs visages ou éléments hors du cadre vertical ({contained_faces}/{total_faces} visages conservables)"
        if total_faces and face_frames < 3:
            return "blur", "sujet détecté trop ponctuellement pour recadrer en toute sécurité"
        if edge_scores and sorted(edge_scores)[len(edge_scores)//2] >= 0.78:
            return "crop", "activité visuelle concentrée dans la zone centrale"
        return "blur", "pas de sujet central suffisamment détectable : image entière conservée"
    except Exception as e:
        # Safe fallback; don't crop wide material blindly.
        if ratio <= 0.78:
            return "crop", "source portrait : adaptation plein écran"
        if ratio >= 1.55:
            return "blur", "source large et analyse visuelle indisponible : image entière conservée"
        return "blur", "analyse visuelle indisponible : choix prudent avec fond flou"











def studio_page():
    session_id = ACTIVE_SESSION_ID.get()
    s = load_studio()
    d = load_data()
    source = s.get("source")
    source_name = Path(source).name if source else "Aucune vidéo chargée"
    source_w = int(s.get("width") or 0)
    source_h = int(s.get("height") or 0)
    source_info = f"{source_w} × {source_h}" if source_w and source_h else "dimensions non détectées"
    source_ratio = (source_w / source_h) if source_w and source_h else 0
    if source_ratio and source_ratio < 0.80:
        source_advice = "<div class='note' style='border:1px solid #ff7d89'><b>Attention : source verticale détectée.</b> Si ce fichier est déjà un clip 9:16 avec fond flouté, ne le réutilise pas comme source. Recharge la vidéo originale horizontale/panoramique (par exemple 3840 × 1634) pour éviter un effet d’image miniature dans l’image.</div>"
    elif source_ratio and source_ratio > 1.70:
        source_advice = "<div class='note'><b>Source panoramique détectée :</b> le plan original sera affiché sur toute la largeur, avec le fond flouté au-dessus et en dessous.</div>"
    else:
        source_advice = "<div class='note'>La source sera adaptée au canevas vertical sans être réduite dans un cadre portrait plus petit.</div>"
    is_busy = session_worker_active(session_id)
    transcript_data = s.get("transcript") or {}
    segments = transcript_data.get("segments", [])
    duration = float(s.get("duration", 0) or 0)
    suggestions = s.get("suggestions", []) or []
    scan_full = bool(s.get("scan_full", False))
    analysis_mode = s.get("analysis_mode", "highlights")
    selection_start = float(s.get("selection_start", transcript_data.get("range_start", 0)) or 0)
    selection_end = float(s.get("selection_end", transcript_data.get("range_end", min(duration, 45))) or min(duration, 45))
    clip_start_default = float(transcript_data.get("range_start", selection_start) if transcript_data else selection_start)
    clip_end_default = float(transcript_data.get("range_end", selection_end) if transcript_data else selection_end)
    vocabulary_prompt = str(s.get("transcription_prompt") or default_transcription_prompt(source_name))
    next_title = suggested_clip_title(s, d)
    if scan_full:
        scan_note = ("La transcription intégrale sert à détecter les phrases et les transitions narratives. Sélectionne un épisode pour le retranscrire précisément et préparer ses sous-titres." if s.get("operation") == "parts" else "La transcription complète a servi à classer les passages. Sélectionne un extrait puis retranscris cette seule plage pour préparer ses sous-titres.")
        seg_html = "<div class='note'>" + esc(scan_note) + "</div>"
    else:
        segment_rows = []
        for i, x in enumerate(segments):
            low_words = [str(w.get("word", "")).strip() for w in (x.get("words") or [])
                         if float(w.get("probability", 1.0) or 0.0) < 0.62 and str(w.get("word", "")).strip()]
            low_note = ("<div class='confidence-note'>À vérifier (confiance faible) : " + esc(", ".join(low_words[:8])) + ("…" if len(low_words) > 8 else "") + "</div>") if low_words else ""
            segment_rows.append(
                f"<div class='edit-line'><span class='time'>{fmt_time(x['start'])} – {fmt_time(x['end'])}</span>"
                f"<div><textarea name='seg_{i}' rows='2' aria-label='Corriger le segment {i+1}'>{esc(x.get('text',''))}</textarea>{low_note}</div></div>"
            )
        seg_html = "".join(segment_rows) or "<p class='muted'>La transcription apparaîtra ici après analyse.</p>"
    saved_parts = s.get("parts", []) or []
    parts_rows = []
    for part in saved_parts:
        idx = int(part.get("index", len(parts_rows) + 1))
        pstart, pend = float(part.get("start", 0)), float(part.get("end", 0))
        parts_rows.append(
            "<div class='candidate'><div class='candidate-head'><b>Partie " + str(idx) + "</b>"
            + "<span class='pill'>" + f"{pend-pstart:.1f} s" + "</span></div>"
            + "<div class='candidate-quote'>" + fmt_time(pstart) + " → " + fmt_time(pend) + "</div>"
            + ("<div class='candidate-reasons'>" + str(int(part.get("sequence_count"))) + " séquences narratives regroupées</div>"
               if part.get("sequence_count") is not None else
               ("<div class='confidence-note'>Transcription trop clairsemée : coupure de secours à faible confiance.</div>"
                if part.get("boundary_confidence") == "low_transcript_evidence" else ""))
            + "<div class='candidate-quote'>" + esc(part.get("teaser", "")) + "</div>"
            + "<div class='candidate-reasons'>Justification de la coupure : " + esc(part.get("reason", "transition de séquence")) + "</div>"
            + "<form method='post' action='/select-part'><input type='hidden' name='start' value='" + f"{pstart:.3f}" + "'><input type='hidden' name='end' value='" + f"{pend:.3f}" + "'><button class='secondary'>Préparer cette partie</button></form></div>"
        )
    parts_html = "".join(parts_rows) if parts_rows else "<p class='muted'>Aucune partie définie pour le moment.</p>"
    output = s.get("last_output")
    output_html = f"<div class='status'><b class='good'>Clip terminé.</b><br>{esc(Path(output).name)}<br><a class='btn' href='/files/{esc(Path(output).name)}' download>Télécharger le MP4</a></div>" if output and Path(output).exists() else ""
    alignment_state = transcript_data.get("alignment_status", "non effectué") if transcript_data else "en attente"
    alignment_message = transcript_data.get("alignment_message", "") if transcript_data else ""
    if alignment_state == "acoustique":
        aligned_n = int(transcript_data.get("aligned_words", len(transcript_data.get("words", []))) or 0)
        total_n = int(transcript_data.get("total_words", len(transcript_data.get("words", []))) or 0)
        alignment_html = f"<div class='status'><b class='good'>Réalignement acoustique terminé.</b><br>{aligned_n}/{total_n} mots placés sur la voix avec le modèle français Wav2Vec2.<br><span class='muted'>{esc(alignment_message)}</span></div>"
    elif alignment_state in ("échec", "non installé", "à refaire", "secours_whisper"):
        alignment_html = f"<div class='status'><b class='warn'>Synchronisation acoustique : {esc(alignment_state)}.</b><br><span class='muted'>{esc(alignment_message)}</span></div>"
    else:
        alignment_html = f"<div class='status'><b>Synchronisation :</b> {esc(alignment_state)}. <span class='muted'>{esc(alignment_message)}</span></div>"
    realign_button = ""  # Le réalignement est désormais automatique à la création du clip.
    refresh = bool(is_busy)
    candidate_html = ""
    if suggestions:
        cards=[]
        annotations = load_annotations()["items"]
        rating_labels = {"excellent": "Excellent extrait", "good": "Bon extrait", "medium": "Moyen", "bad": "Mauvais", "unusable": "Inutilisable"}
        reason_labels = {"bad_start": "Mauvais début", "bad_end": "Mauvaise fin", "missing_context": "Manque de contexte", "not_interesting": "Pas assez intéressant", "repetitive": "Trop répétitif", "bad_transcription": "Mauvaise transcription", "too_much_silence": "Trop de silence", "bad_audiovisual_interest": "Mauvais intérêt audiovisuel", "multiple_scenes_or_topics": "Plusieurs scènes ou sujets"}
        for i, c in enumerate(suggestions, 1):
            quote = str(c.get("text", ""))
            reasons = " · ".join(c.get("reasons", []))
            scores = c.get("scores", {})
            defects = " · ".join(c.get("defects", [])) or "Aucun défaut évident détecté dans la transcription"
            annotation_key = candidate_id(source, c)
            annotation = annotations.get(annotation_key, {})
            rating_options = "<option value=''>À évaluer</option>" + "".join(
                f"<option value='{rating}'{' selected' if annotation.get('rating') == rating else ''}>{rating_labels[rating]}</option>" for rating in RATINGS)
            reason_checks = " ".join(
                f"<label style='display:inline-block;margin:4px 8px 4px 0'><input type='checkbox' name='reason' value='{key}'{' checked' if key in annotation.get('reasons', []) else ''}> {reason_labels[key]}</label>" for key in REASONS)
            annotation_form = (f"<details class='optional-annotation'><summary>Évaluer cette proposition (facultatif)</summary><form method='post' action='/annotate-candidate'><input type='hidden' name='candidate_id' value='{annotation_key}'>"
                f"<label>Évaluation humaine</label><select name='rating'>{rating_options}</select><div>{reason_checks}</div>"
                f"<label>Commentaire facultatif</label><textarea name='comment' rows='2' maxlength='1000'>{esc(annotation.get('comment',''))}</textarea>"
                f"<button class='secondary'>Enregistrer l'évaluation</button></form></details>")
            cards.append(
                f"<div class='candidate' id='candidate-{i}' data-score='{c['score']}' data-transcript='{scores.get('transcript_quality',0)}' data-autonomy='{scores.get('autonomy',0)}'><div class='candidate-head'><div><b>#{i} · {fmt_time(c['start'])} → {fmt_time(c['end'])}</b> <span class='pill'>{c['duration']:.1f} s</span></div><div class='score'>{c['score']:.0f} · indice de tri</div></div>"
                f"<h3>{esc(c.get('title') or 'Passage proposé')}</h3><div class='candidate-quote'>{esc(quote)}</div><div class='candidate-reasons'>{esc(reasons)}</div>"
                f"<details><summary>Détails et points à vérifier</summary><p>Intérêt narratif estimé par heuristique : {scores.get('narrative_interest','—')}/100 · Compréhensibilité estimée : {scores.get('comprehensibility','—')}/100 · Autonomie : {scores.get('autonomy','—')}/100 · Qualité de transcription : {scores.get('transcript_quality','—')}/100 · Audio : non analysé · Visuel : non analysé · Contraintes de campagne : respectées</p><p>Confiance moyenne Whisper : {c.get('confidence',0)*100:.0f} % · Couverture de parole : {c.get('speech_coverage',0)*100:.0f} %</p><p>{esc(defects)}</p><small>{esc(c.get('score_method',''))}. Ces estimations ne mesurent pas la viralité.</small></details>"
                f"<div class='candidate-actions'><button type='button' class='secondary' data-preview-start='{c['start']:.3f}' data-preview-end='{c['end']:.3f}'>▶ Prévisualiser</button>"
                f"<button type='button' class='candidate-pick' data-candidate-start='{c['start']:.3f}' data-candidate-end='{c['end']:.3f}'>Choisir cet extrait</button></div>{annotation_form}</div>"
            )
        candidate_html = ("<div class='card' id='candidate-results'><h2>Passages proposés</h2>"
            "<p class='muted'>Ne te base pas uniquement sur le texte : lance la prévisualisation pour voir et entendre chaque passage dans son contexte avant de le choisir.</p>"
            f"<p class='muted'>La notation est facultative : les propositions peuvent être prévisualisées et choisies sans remplir d'évaluation. Les avis enregistrés servent uniquement à la validation et n'entraînent pas le moteur.</p>"
            "<label for='candidate-sort'>Trier les propositions</label><select id='candidate-sort'><option value='score'>Indice global</option><option value='transcript'>Qualité de transcription</option><option value='autonomy'>Autonomie</option></select>"
            "<div id='candidate-preview-panel' hidden><video id='candidate-preview-video' class='candidate-preview' controls preload='metadata' playsinline src='/source-preview'></video><p id='candidate-preview-label' class='muted'>Prévisualisation du passage sélectionné.</p></div>"
            + "<div id='candidate-list'>" + "".join(cards) + "</div></div>"
            + "<div class='candidate-selected' id='candidate-selected-summary' hidden><b>Extrait sélectionné</b><p id='candidate-selected-text' class='muted'></p><button type='button' class='secondary' id='candidate-change'>Changer d'extrait</button></div>")
    elif scan_full and transcript_data and s.get("operation") == "candidates":
        candidate_html = "<div class='card' id='candidate-results'><h2>Aucun passage proposé automatiquement</h2><p class='muted'>" + esc(s.get("message", "Aucun passage exploitable n'a été repéré.")) + "</p><p>Tu peux vérifier la transcription, essayer une plage plus longue ou choisir manuellement les temps d'un passage.</p></div>"
    reference_html = (
        "<div class='card' id='reference-review'><h2>Outil facultatif de validation</h2>"
        "<p class='muted'>Cette revue indépendante est réservée à l'évaluation et au développement. Elle n'est pas nécessaire pour générer, choisir ou exporter des clips.</p>"
        f"<a class='btn secondary' href='/reference-review?sid={esc(session_id)}'>Ouvrir la revue facultative de la source</a></div>"
        if source else ""
    )
    mode_options = (
        "<option value='highlights'" + (" selected" if analysis_mode == "highlights" else "") + ">Trouver les meilleurs extraits indépendants</option>"
        + "<option value='story_parts'" + (" selected" if analysis_mode == "story_parts" else "") + ">Découper en épisodes qui se suivent</option>"
    )
    selected_campaign_id = str(s.get("campaign_id") or "")
    no_campaign_selected = " selected" if not selected_campaign_id else ""
    campaign_options = f"<option value=''{no_campaign_selected}>Aucune campagne · 20–45 s</option>" + "".join(
        f"<option value='{esc(c.get('id',''))}'{' selected' if str(c.get('id','')) == selected_campaign_id else ''}>{esc(c.get('name','Campagne sans nom'))} · {int(c.get('min_duration',20) or 20)}–{int(c.get('max_duration',45) or 45)} s</option>"
        for c in d.get("campaigns", [])
    )
    if analysis_mode == "story_parts":
        workflow_action = (
            "<form method='post' action='/split-parts'><label>Nombre d'épisodes à créer</label>"
            + f"<input name='count' type='number' min='2' max='30' step='1' value='{int(s.get('parts_count', 4) or 4)}'>"
            + f"<label>Noms propres / vocabulaire à reconnaître (facultatif)</label><input name='vocabulary' type='text' value='{esc(vocabulary_prompt)}' placeholder='Noms, lieux, jeux, expressions...'>"
            + "<button class='secondary' " + ("disabled" if not source or is_busy else "") + ">Transcrire la vidéo et proposer les coupures narratives</button></form>"
            + "<p class='muted'>Le moteur repère d'abord des séquences probables à partir du texte autour, des amorces répétées et des pauses de parole, puis regroupe ces séquences dans l'ordre chronologique. La durée cible reste souple; les coupures ne se font qu'aux limites détectées. Les changements de plan seuls ne provoquent pas de coupure. Les signaux visuels et la diarisation des intervenants ne sont pas encore analysés.</p>"
            + parts_html
        )
    else:
        workflow_action = (
            "<form method='post' action='/find-candidates'><input type='hidden' name='model' value='small'>"
            + ("<label>Campagne et durée cible</label><select name='campaign_id'>" + campaign_options + "</select>" if campaign_options else "<p class='muted'>Ajoute une campagne pour appliquer ses contraintes de durée.</p>")
            + "<label>Nombre maximal de propositions</label><input type='number' name='candidate_limit' min='1' max='30' value='8'>"
            + f"<label>Noms propres / vocabulaire à reconnaître (facultatif)</label><input name='vocabulary' type='text' value='{esc(vocabulary_prompt)}' placeholder='Noms, lieux, jeux, expressions...'>"
            + "<input type='hidden' name='start' value='0'><input type='hidden' name='end' value='" + f"{duration:.3f}" + "'>"
            + "<button class='secondary' " + ("disabled" if not source or is_busy else "") + ">Analyser toute la vidéo et trouver les meilleurs extraits</button></form>"
            + "<p class='muted'>Les durées viennent de la campagne sélectionnée. Le nombre est une limite maximale : les propositions similaires ou faibles sont écartées. Les notes sont des indices heuristiques basés sur la transcription, pas une prédiction de performance.</p>"
        )
    workflow_panel = (
        "<div class='card' id='workflow-card'><h2>2. Que souhaites-tu produire ?</h2>"
        + "<form method='post' action='/workflow-mode'><label>Mode de traitement</label><select name='mode' onchange='this.form.submit()'>" + mode_options + "</select></form>"
        + workflow_action
        + "<div class='status operation-status'><b>État de l'analyse :</b> <span id='analysis-status-value'>" + esc(s.get('status', 'En attente') if s.get('operation') in ('candidates','parts') else 'En attente') + "</span><br><span id='analysis-status-message' class='muted'>" + esc(s.get('message', '') if s.get('operation') in ('candidates','parts') else 'Choisis un mode puis lance son analyse.') + "</span></div></div>"
    )
    body = f"""
{studio_tabs_html(session_id)}
<div class="card" id="source-card"><h2>1. Charger la vidéo source</h2><p class="muted">Sélectionne le fichier original fourni par le créateur ou autorisé par la campagne. Le chargement démarre automatiquement dès que tu choisis le fichier.</p><form id="source-upload-form" method="post" action="/upload" enctype="multipart/form-data"><label>Fichier vidéo</label><input id="source-file-input" type="file" name="video" accept="video/*,.mp4,.mov,.mkv,.webm,.m4v" required onchange="if(this.files && this.files.length){{document.getElementById('upload-hint').textContent='Chargement de '+this.files[0].name+'… Ne ferme pas cette page.';this.form.requestSubmit();}}"><div id="upload-hint" class="upload-hint">Le fichier sera chargé automatiquement après sélection. Les gros fichiers peuvent prendre quelques minutes.</div></form><div class="status">Vidéo actuelle : <b>{esc(source_name)}</b>{f' · {fmt_time(duration)}' if duration else ''} · {esc(source_info)}</div>{source_advice}</div>
{workflow_panel}
<div class="card" id="transcribe-card"><h2>3. Transcrire un passage choisi pour préparer ses sous-titres</h2><p class="muted">Les temps ci-dessous sont appliqués <b>avant</b> Whisper. Seule cette portion audio sera extraite puis transcrite.</p><form id="transcribe-form" method="post" action="/transcribe"><div class="cols"><div><label>Début de l'extrait (secondes)</label><input id="transcribe-start" name="start" type="text" inputmode="decimal" data-decimal="1" value="{selection_start:.2f}" required></div><div><label>Fin de l'extrait (secondes)</label><input id="transcribe-end" name="end" type="text" inputmode="decimal" data-decimal="1" value="{selection_end:.2f}" required></div></div><label>Noms propres / vocabulaire à reconnaître (facultatif)</label><input name="vocabulary" type="text" value="{esc(vocabulary_prompt)}" placeholder="Noms, lieux, jeux, expressions..."><p class="muted">Ces mots servent de contexte à Whisper pour mieux reconnaître les noms propres et les termes spécialisés.</p><label>Modèle de transcription</label><select name="model"><option value="small" selected>small – bon compromis vitesse / précision</option><option value="base">base – plus rapide, moins précis</option><option value="medium">medium – plus précis, nettement plus lent sur CPU</option></select><button {'disabled' if not source or is_busy else ''}>Transcrire uniquement cet extrait</button></form>
<div class="status operation-status"><b>État de la transcription :</b> <span id="transcribe-status-value">{esc(s.get('status', 'En attente') if s.get('operation') == 'transcribe' else 'En attente')}</span><br><span id="transcribe-status-message" class="muted">{esc(s.get('message', '') if s.get('operation') == 'transcribe' else 'Après avoir choisi un passage ou un épisode, tu peux le transcrire pour préparer les sous-titres.')}</span></div></div>
{candidate_html}
{reference_html}
<div class="card" id="clip-card"><h2>4. Créer le clip à partir de l'extrait transcrit</h2><p class="muted">La durée finale est vérifiée selon la campagne sélectionnée. Le clip doit rester dans la zone transcrite. Le réalignement est tenté avant export ; si le moteur est indisponible, les horaires Whisper restent utilisés en secours.</p><form id="make-clip-form" method="post" action="/make-clip"><label>Campagne (facultatif)</label><select name="campaign_id">{campaign_options}</select><div class="cols"><div><label>Début (secondes)</label><input id="clip-start" name="start" type="text" inputmode="decimal" data-decimal="1" value="{clip_start_default:.2f}" required></div><div><label>Fin (secondes)</label><input id="clip-end" name="end" type="text" inputmode="decimal" data-decimal="1" value="{clip_end_default:.2f}" required></div></div><label>Accroche à l'écran (facultatif)</label><input name="hook" maxlength="80" placeholder="Ex. 3 mondes. Lequel tu choisis ?"><label>Style de cadrage</label><select name="framing"><option value="auto" selected>Détection automatique : flou ou plein écran zoomé</option><option value="blur">Vidéo entière avec fond flou</option><option value="crop">Plein écran zoomé (recadrage vertical)</option></select><p class="muted">En automatique, Clip Factory choisit entre conserver toute l’image avec un fond flou et recadrer en plein écran zoomé. Tu peux aussi imposer l’un des deux rendus.</p><label>Réglage de synchronisation des sous-titres (secondes)</label><input name="subtitle_delay" type="text" inputmode="decimal" data-decimal="1" value="0,00"><p class="muted">0 = horodatage Whisper. Valeur positive = retarde les sous-titres ; valeur négative = les avance. Ajuste par petits pas de 0,10 s après visionnage.</p><p class="emphasis-help">Les sous-titres restent blancs ; un terme important peut prendre légèrement de l’ampleur avec une animation discrète, sans couleur criarde ni effet karaoké.</p><label>Titre du clip</label><input name="title" maxlength="100" value="{esc(next_title)}"><button {'disabled' if not source or not s.get('transcript') or is_busy else ''}>Créer le clip MP4 sous-titré</button></form><div class="status operation-status"><b>Création du clip :</b> <span id="clip-status-value">{esc(s.get('status', 'En attente') if s.get('operation') == 'clip' else 'En attente')}</span><br><span id="clip-status-message" class="muted">{esc(s.get('message', '') if s.get('operation') == 'clip' else 'Le résultat de la création apparaîtra ici.')}</span></div>{output_html}<p class="muted">La transcription est horodatée sur la timeline originale : les sous-titres resteront correctement synchronisés.</p></div>
<div class="card" id="transcript-card"><h2>5. Corriger la transcription</h2><p class="muted">Relis les blocs ci-dessous et corrige les mots mal reconnus (noms, lieux, expressions, ponctuation). Le réalignement acoustique sera automatiquement relancé à la prochaine création du clip si tu modifies le texte.</p><div class="edit-help">Le réalignement acoustique recherche la place de chaque mot dans la forme sonore au moment de l'export. Il ne corrige pas l'orthographe : corrige d'abord les mots puis enregistre. Les chevauchements de voix et les mots absents de l'audio restent des cas difficiles.</div><p class="muted">{len(segments)} segments · langue détectée : {esc((s.get('transcript') or {}).get('language', '—'))} · extrait transcrit : {fmt_time((s.get('transcript') or {}).get('range_start', 0))} à {fmt_time((s.get('transcript') or {}).get('range_end', duration))}</p>{('<form method="post" action="/save-transcript"><div class="transcript">'+seg_html+'</div>'+('<button>Enregistrer les corrections</button>' if segments else '')+'</form>') if not scan_full else seg_html}<div class="status"><b>État des corrections :</b> {esc(s.get('edit_message','Aucune correction enregistrée.'))}<br><span class='muted'>Les corrections seront automatiquement réalignées lors de la prochaine création du clip.</span></div>{alignment_html}</div>
<div class="note"><b>Réalignement local :</b> les mots sont d'abord reconnus par Whisper, puis un modèle acoustique CTC français (Wav2Vec2) recale leurs horodatages sur l'audio. Le réalignement est automatique après transcription et sera relancé lors de la création du clip si le texte est corrigé.</div>
"""
    # Bind every studio form, preview and poll to its own persistent tab/session.
    sid = ACTIVE_SESSION_ID.get()
    body = re.sub(r'action=(["\'])(/[^"\']*)\1',
                  lambda m: f'action={m.group(1)}{m.group(2)}' + ("&" if "?" in m.group(2) else "?") + f"sid={sid}" + m.group(1), body)
    body = body.replace("src='/source-preview'", f"src='/source-preview?sid={sid}'")
    body = body.replace('src="/source-preview"', f'src="/source-preview?sid={sid}"')
    return page(body, "Atelier vidéo — Clip Factory", refresh=refresh, session_id=sid)


def reference_review_page():
    """Blind source-review view: deliberately contains no algorithm suggestions."""
    sid = ACTIVE_SESSION_ID.get()
    studio = load_studio()
    source = studio.get("source")
    if not source:
        return page("<div class='card'><h2>Aucune source chargée dans cette session.</h2><a class='btn' href='/studio'>Retour à l'atelier</a></div>",
                    "Jeu de référence indépendant", session_id=sid)
    source_name = Path(source).name
    duration = float(studio.get("duration", 0) or 0)
    references = load_reference_spans().get("items", {})
    source_references = [item for item in references.values() if item.get("source") == source_name]
    catalog = load_source_catalog().get("items", {})
    metadata = next((value for value in catalog.values() if value.get("source") == source_name), {})
    rating_labels = {"excellent": "Excellent", "good": "Bon", "medium": "Moyen", "bad": "Faible", "unusable": "Inutilisable"}
    type_labels = {"podcast": "Podcast", "interview": "Interview", "entertainment": "Divertissement", "humor": "Humour", "gaming_commentary": "Gaming commenté", "conversation": "Conversation", "narrative": "Vidéo narrative", "other": "Autre"}
    type_options = "<option value=''>Type de contenu…</option>" + "".join(
        f"<option value='{kind}'{' selected' if metadata.get('content_type') == kind else ''}>{type_labels[kind]}</option>" for kind in SOURCE_TYPES)
    interest_options = "<option value=''>Choisir une appréciation…</option>" + "".join(
        f"<option value='{rating}'>{rating_labels[rating]}</option>" for rating in RATINGS)
    rows = "".join(
        f"<div class='candidate'><b>{fmt_time(float(item.get('start',0)))} → {fmt_time(float(item.get('end',0)))}</b> · {esc(rating_labels.get(item.get('narrative_interest'), item.get('narrative_interest','')))}"
        f"<div class='candidate-reasons'>{esc(type_labels.get(item.get('content_type'), item.get('content_type','')))}</div>"
        f"<div class='candidate-quote'>{esc(item.get('justification',''))}</div></div>"
        for item in sorted(source_references, key=lambda row: float(row.get("start", 0)))
    ) or "<p class='muted'>Aucun passage de référence enregistré pour cette source.</p>"
    body = (
        studio_tabs_html(sid)
        + "<div class='card' id='reference-review'><h1>Outil facultatif de validation indépendante</h1>"
        + f"<p class='muted'>Source : <b>{esc(source_name)}</b> · {fmt_time(duration)} · Les propositions automatiques et leurs notes sont masquées dans cette vue.</p>"
        + "<p>Cet outil sert uniquement à évaluer Clip Factory pendant son développement. Il n'est pas une étape de production. Si tu choisis de l'utiliser, regarde la vidéo originale et repère des plages intéressantes; les temps sont enregistrés sur la timeline originale. Cette annotation ne lit ni ne copie la transcription, et ne dépend d'aucune suggestion du moteur.</p>"
        + f"<video class='candidate-preview' controls preload='metadata' playsinline src='/source-preview?sid={esc(sid)}'></video>"
        + f"<p class='muted'>{len(source_references)} passage(s) indépendant(s) annoté(s) pour cette source.</p>"
        + f"<form method='post' action='/annotate-missed?sid={esc(sid)}'><div class='cols'><div><label>Début original (secondes)</label><input type='number' name='start' min='0' max='{duration:.3f}' step='0.01' value='0.00' required></div>"
        + f"<div><label>Fin originale (secondes)</label><input type='number' name='end' min='0.01' max='{duration:.3f}' step='0.01' value='{min(duration,45.0):.2f}' required></div></div>"
        + f"<label>Type principal de contenu</label><select name='content_type' required>{type_options}</select>"
        + f"<label>Intérêt narratif</label><select name='narrative_interest' required>{interest_options}</select>"
        + "<label>Justification facultative</label><textarea name='justification' rows='3' maxlength='1000'></textarea>"
        + "<button>Enregistrer ce passage de référence</button></form></div>"
        + f"<div class='card'><h2>Références manuelles enregistrées</h2>{rows}</div>"
        + f"<a class='btn secondary' href='/studio?sid={esc(sid)}'>Retour à l'atelier</a>"
    )
    return page(body, "Jeu de référence indépendant — Clip Factory", session_id=sid)

def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def get_duration(path):
    exe = ffmpeg_exe()
    proc = subprocess.run([exe, "-i", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", timeout=120)
    text = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        raise RuntimeError("Impossible de lire la durée de la vidéo. Vérifie le fichier source.")
    return int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))

def get_video_size(path):
    """Return the dimensions of the first real video stream, ignoring attached cover art when possible."""
    exe = ffmpeg_exe()
    proc = subprocess.run([exe, "-i", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
    text = proc.stderr
    for line in text.splitlines():
        if "Video:" not in line or "attached pic" in line.lower():
            continue
        match = re.search(r"(?<!\d)(\d{2,6})x(\d{2,6})(?!\d)", line)
        if match:
            return int(match.group(1)), int(match.group(2))
    return 0, 0

def aligner_python():
    """Dedicated isolated environment for PyTorch/Transformers acoustic alignment."""
    candidate = ROOT / ".alignenv" / "Scripts" / "python.exe"
    return candidate if candidate.exists() else None


def run_acoustic_alignment(transcript, audio_path):
    """Realign the current (possibly manually corrected) segment text against the source WAV."""
    py = aligner_python()
    if not py:
        raise RuntimeError("Le moteur acoustique n'est pas installé. Lance une seule fois INSTALLER_ALIGNEMENT.bat dans le dossier Clip_Factory.")
    helper = ROOT / "forced_align.py"
    if not helper.exists():
        raise RuntimeError("Le fichier forced_align.py est absent. Réinstalle la mise à jour V0.7 complète.")
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError("Le fichier audio de réalignement est introuvable. Recharge la vidéo et relance la transcription.")

    range_start = float(transcript.get("range_start", 0.0) or 0.0)
    payload_segments = []
    for seg in transcript.get("segments", []):
        local_start = max(0.0, float(seg.get("start", range_start)) - range_start)
        local_end = max(local_start + 0.01, float(seg.get("end", range_start + 0.1)) - range_start)
        old_words = []
        for word in seg.get("words", []) or []:
            old_words.append({
                "word": word.get("word", ""),
                "start": max(0.0, float(word.get("start", range_start)) - range_start),
                "end": max(0.01, float(word.get("end", range_start + 0.1)) - range_start),
            })
        payload_segments.append({"start": local_start, "end": local_end,
                                 "text": str(seg.get("text", "")), "words": old_words})

    temp_dir = MEDIA / "temp_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:10]
    input_json = temp_dir / f"align_input_{token}.json"
    output_json = temp_dir / f"align_output_{token}.json"
    payload = {"audio_path": str(audio_path.resolve()), "segments": payload_segments}
    input_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run([str(py), str(helper), str(input_json), str(output_json)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=2400, creationflags=creationflags)
        if not output_json.exists():
            detail = (proc.stderr or proc.stdout or "Aucun résultat du moteur acoustique.")[-1800:]
            raise RuntimeError(detail)
        aligned = json.loads(output_json.read_text(encoding="utf-8"))
        if not aligned.get("ok"):
            raise RuntimeError(aligned.get("error", proc.stderr[-1800:] or "Échec du réalignement."))

        offset = range_start
        new_segments = aligned.get("segments", [])
        if len(new_segments) != len(transcript.get("segments", [])):
            raise RuntimeError("Le moteur acoustique a renvoyé un nombre de segments inattendu.")
        flat_words = []
        for original_seg, aligned_seg in zip(transcript["segments"], new_segments):
            new_words = []
            for word in aligned_seg.get("words", []):
                item = dict(word)
                item["start"] = round(offset + float(item.get("start", 0)), 3)
                item["end"] = round(offset + float(item.get("end", 0.01)), 3)
                new_words.append(item)
            original_seg["words"] = new_words
            flat_words.extend(new_words)
        transcript["words"] = flat_words
        transcript["alignment_status"] = "acoustique"
        transcript["alignment_model"] = aligned.get("alignment_model", "Wav2Vec2 français (Apache 2.0)")
        transcript["aligned_words"] = int(aligned.get("aligned_words", len(flat_words)))
        transcript["total_words"] = int(aligned.get("total_words", len(flat_words)))
        transcript["alignment_audio"] = str(audio_path.resolve())
        transcript["alignment_message"] = (
            f"{transcript['aligned_words']} mots alignés acoustiquement sur {transcript['total_words']} "
            "(les mots non reconnaissables par le modèle gardent leur ancien timing)."
        )
        return transcript
    finally:
        for p in (input_json, output_json):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def ensure_alignment_audio(transcript, source):
    """Return a persistent 16 kHz mono WAV for later re-alignment after text edits."""
    saved = transcript.get("alignment_audio")
    if saved and Path(saved).exists():
        return Path(saved)
    range_start = float(transcript.get("range_start", 0))
    range_end = float(transcript.get("range_end", 0))
    if not source or not Path(source).exists() or range_end <= range_start:
        raise RuntimeError("Impossible de retrouver l'audio d'origine. Recharge la source et relance la transcription.")
    temp_dir = MEDIA / "temp_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)
    audio_path = temp_dir / f"align_saved_{uuid.uuid4().hex[:10]}.wav"
    command = [ffmpeg_exe(), "-y", "-i", str(source), "-ss", f"{range_start:.3f}",
               "-t", f"{range_end-range_start:.3f}", "-vn", "-ac", "1", "-ar", "16000",
               "-c:a", "pcm_s16le", str(audio_path)]
    proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size < 1000:
        raise RuntimeError("Impossible de réextraire l'audio pour le réalignement : " + (proc.stderr[-1200:] if proc.stderr else "erreur FFmpeg"))
    transcript["alignment_audio"] = str(audio_path.resolve())
    return audio_path


def realign_worker():
    s = load_studio()
    try:
        transcript = s.get("transcript")
        if not transcript or not transcript.get("segments"):
            raise RuntimeError("Transcris d'abord un extrait.")
        source = s.get("source")
        audio_path = ensure_alignment_audio(transcript, source)
        s["operation"] = "align"
        s["status"] = "Réalignement acoustique…"
        s["message"] = "Le modèle acoustique français recherche la position de chaque mot dans l'audio. Le premier chargement du modèle peut prendre un moment."
        save_studio(s)
        transcript = run_acoustic_alignment(transcript, audio_path)
        s = load_studio()
        s["transcript"] = transcript
        s["status"] = "Réalignement acoustique terminé"
        s["message"] = transcript.get("alignment_message", "Horodatages recalculés sur l'audio.")
        s["edit_message"] = "Réalignement acoustique terminé. Les prochains exports utiliseront ces horaires."
        save_studio(s)
        save_transcript_snapshot(transcript)
    except Exception as e:
        s = load_studio()
        s["operation"] = "align"
        if s.get("transcript"):
            s["transcript"]["alignment_status"] = "échec"
            s["transcript"]["alignment_message"] = f"{type(e).__name__}: {e}"
        s["status"] = "Erreur de réalignement"
        s["message"] = f"{type(e).__name__}: {e}"
        s["edit_message"] = "Le réalignement acoustique a échoué. Les anciens horaires n'ont pas été remplacés."
        save_studio(s)


def _collect_whisper_segments(segments_iter, timeline_offset):
    """Materialize one Whisper pass as absolute-timeline segments and words."""
    segments, words = [], []
    for seg in segments_iter:
        local_start = float(seg.start)
        local_end = float(seg.end)
        seg_words = []
        for word in (seg.words or []):
            item = {"start": timeline_offset + float(word.start),
                    "end": timeline_offset + float(word.end), "word": word.word,
                    "probability": float(getattr(word, "probability", 1.0) or 0.0)}
            words.append(item)
            seg_words.append(item)
        segments.append({"start": timeline_offset + local_start,
                         "end": timeline_offset + local_end,
                         "text": seg.text.strip(), "words": seg_words,
                         "avg_logprob": float(getattr(seg, "avg_logprob", 0.0) or 0.0),
                         "no_speech_prob": float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
                         "compression_ratio": float(getattr(seg, "compression_ratio", 0.0) or 0.0)})
    return segments, words


def _should_retry_sparse_vad_scan(scan_for_candidates, suggestions, diagnostics):
    """Retry an analysis pass only when candidate-window evidence shows a VAD miss."""
    if not scan_for_candidates or suggestions:
        return False
    # A blank or sub-10-word pass has no candidate-window evidence. Retrying it
    # tends to spend time transcribing silence/music and can promote hallucinations.
    # The concrete VAD-miss case has 42 usable words and rejected sparse windows.
    return (diagnostics.get("rejected_sparse_windows", 0) > 0 and
            diagnostics.get("candidate_windows_considered", 0) > 0)


def _should_retry_sparse_vad_analysis(analysis_mode, suggestions, diagnostics):
    """Shared VAD recovery gate for independent clips and narrative episodes."""
    return bool(analysis_mode and not suggestions and
                diagnostics.get("rejected_sparse_windows", 0) > 0 and
                diagnostics.get("candidate_windows_considered", 0) > 0)


def _prefer_vad_retry(primary_diagnostics, retry_diagnostics, retry_suggestions):
    """Use the no-VAD pass only if it adds rankable speech and at least one candidate."""
    return bool(retry_suggestions and
                retry_diagnostics.get("normalized_words", 0) >
                primary_diagnostics.get("normalized_words", 0))


def transcribe_worker(model_size, selected_start, selected_end, align_after=True, scan_for_candidates=False, split_into_parts=False, requested_part_count=4, initial_prompt="", candidate_min=20.0, candidate_max=45.0, candidate_limit=8):
    """Transcribe the selected audio excerpt, then force-align its words acoustically."""
    s = load_studio()
    source = Path(s.get("source") or "")
    temp_audio = None
    keep_audio = False
    performance = {"audio_extraction_seconds": 0.0, "asr_seconds": 0.0,
                   "candidate_ranking_seconds": 0.0, "narrative_detection_seconds": 0.0,
                   "episode_construction_seconds": 0.0}
    try:
        duration = float(s.get("duration", 0) or 0)
        if not source.exists():
            raise RuntimeError("La vidéo source est introuvable. Recharge-la dans l'atelier.")
        if selected_start < 0 or selected_end <= selected_start or selected_end > duration + 0.1:
            raise ValueError(f"Extrait invalide. La vidéo dure {duration:.1f} secondes.")
        clip_length = selected_end - selected_start
        if clip_length < 3:
            raise ValueError("Choisis un extrait d'au moins 3 secondes.")

        temp_dir = MEDIA / "temp_audio"
        temp_dir.mkdir(parents=True, exist_ok=True)
        # Keep this audio file so corrected text can be acoustically re-aligned later.
        temp_audio = temp_dir / f"align_{uuid.uuid4().hex[:10]}.wav"
        ffmpeg = ffmpeg_exe()

        s["operation"] = "parts" if split_into_parts else ("candidates" if scan_for_candidates else "transcribe")
        s["status"] = "Extraction de l'extrait audio…"
        s["message"] = (f"Analyse narrative de la vidéo complète ({clip_length/60:.1f} minutes) avant de choisir les coupures." if split_into_parts else f"Préparation de {selected_start:.1f} à {selected_end:.1f} s ({clip_length:.1f} secondes).")
        s["selection_start"] = selected_start
        s["selection_end"] = selected_end
        s["transcript"] = None
        save_studio(s)

        extract_cmd = [ffmpeg, "-y", "-i", str(source), "-ss", f"{selected_start:.3f}",
                       "-t", f"{clip_length:.3f}", "-vn", "-ac", "1", "-ar", "16000",
                       "-c:a", "pcm_s16le", str(temp_audio)]
        stage_started = time.perf_counter()
        extracted = subprocess.run(extract_cmd, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=3600)
        performance["audio_extraction_seconds"] = round(time.perf_counter() - stage_started, 3)
        if extracted.returncode != 0 or not temp_audio.exists() or temp_audio.stat().st_size < 1000:
            detail = extracted.stderr[-1200:] if extracted.stderr else "Aucune piste audio exploitable."
            raise RuntimeError("Impossible d'extraire le son de l'extrait sélectionné : " + detail)

        s = load_studio()
        s["status"] = "Téléchargement du modèle Whisper…"
        s["message"] = "Chargement du modèle pour transcrire uniquement l'extrait choisi."
        save_studio(s)
        stage_started = time.perf_counter()
        from faster_whisper import WhisperModel
        model = WhisperModel(model_size, device="cpu", compute_type="int8")

        s = load_studio()
        s["status"] = "Transcription de l'extrait…"
        s["message"] = (f"Transcription intégrale ({clip_length/60:.1f} minutes) pour analyser le récit." if split_into_parts else f"Reconnaissance du texte sur {clip_length:.1f} secondes en français.")
        save_studio(s)
        analysis_pass = bool(scan_for_candidates or split_into_parts)
        # VAD is enabled for long-form analysis to avoid hallucinating words over music / silence.
        # For a short user-selected clip, keep the raw timeline and its original Whisper behavior.
        segments_iter, info = model.transcribe(
            str(temp_audio), language="fr", word_timestamps=True,
            vad_filter=analysis_pass,
            vad_parameters={"threshold": 0.48, "min_speech_duration_ms": 250,
                            "min_silence_duration_ms": 450, "speech_pad_ms": 250} if analysis_pass else None,
            beam_size=8, condition_on_previous_text=not analysis_pass, temperature=0.0,
            initial_prompt=(initial_prompt.strip() or None),
            compression_ratio_threshold=2.15 if analysis_pass else 2.4,
            log_prob_threshold=-0.8 if analysis_pass else -1.0,
            no_speech_threshold=0.52 if analysis_pass else 0.6)
        segments, words = _collect_whisper_segments(segments_iter, selected_start)
        performance["asr_seconds"] += time.perf_counter() - stage_started

        result = {"language": getattr(info, "language", "fr"), "segments": segments,
                  "words": words, "model": model_size,
                  "range_start": selected_start, "range_end": selected_end,
                  "range_duration": clip_length, "initial_prompt": initial_prompt, "alignment_audio": str(temp_audio.resolve()),
                  "alignment_status": "non installé" if not aligner_python() else "en attente",
                  "alignment_message": "Le moteur acoustique n'est pas encore installé." if not aligner_python() else "Réalignement acoustique en cours.",
                  "total_words": len(words), "aligned_words": 0}
        suggestions = []
        primary_diagnostics = {}
        if scan_for_candidates or split_into_parts:
            # Both full-video analysis modes must rank the same primary transcript
            # before deciding whether a no-VAD recovery is justified. This avoids
            # retrying healthy transcripts or basing episode boundaries on a
            # separate ASR pass from independent-clip candidates.
            stage_started = time.perf_counter()
            suggestions = rank_candidate_excerpts(result, candidate_min, candidate_max,
                                                  candidate_limit, diagnostics=primary_diagnostics)
            performance["candidate_ranking_seconds"] += time.perf_counter() - stage_started
            if _should_retry_sparse_vad_analysis(True, suggestions, primary_diagnostics):
                s = load_studio()
                s["status"] = "Vérification d'une transcription trop clairsemée…"
                s["message"] = "La première passe a écarté des fenêtres pour couverture vocale insuffisante. Vérification Whisper sans filtre VAD, avec les mêmes garde-fous de qualité."
                save_studio(s)
                stage_started = time.perf_counter()
                retry_iter, retry_info = model.transcribe(
                    str(temp_audio), language="fr", word_timestamps=True,
                    vad_filter=False, vad_parameters=None,
                    beam_size=8, condition_on_previous_text=False, temperature=0.0,
                    initial_prompt=(initial_prompt.strip() or None),
                    compression_ratio_threshold=2.15, log_prob_threshold=-0.8,
                    no_speech_threshold=0.52)
                retry_segments, retry_words = _collect_whisper_segments(retry_iter, selected_start)
                performance["asr_seconds"] += time.perf_counter() - stage_started
                retry_result = dict(result)
                retry_result.update({"language": getattr(retry_info, "language", result["language"]),
                                     "segments": retry_segments, "words": retry_words,
                                     "total_words": len(retry_words)})
                retry_diagnostics = {}
                stage_started = time.perf_counter()
                retry_suggestions = rank_candidate_excerpts(
                    retry_result, candidate_min, candidate_max, candidate_limit,
                    diagnostics=retry_diagnostics)
                performance["candidate_ranking_seconds"] += time.perf_counter() - stage_started
                recovery = {
                    "trigger": "speech-coverage rejections",
                    "method": "same Whisper model and parameters, vad_filter=False",
                    "primary_rankable_words": primary_diagnostics.get("normalized_words", 0),
                    "retry_rankable_words": retry_diagnostics.get("normalized_words", 0),
                    "retry_candidates": len(retry_suggestions),
                    "used": _prefer_vad_retry(primary_diagnostics, retry_diagnostics,
                                              retry_suggestions),
                }
                if recovery["used"]:
                    result = retry_result
                    suggestions = retry_suggestions
                result["transcription_recovery"] = recovery
        s = load_studio()
        s["transcript"] = result
        s["selection_start"] = selected_start
        s["selection_end"] = selected_end
        s["status"] = "Transcription de l'extrait terminée"
        s["message"] = f"{len(segments)} segments et {len(words)} mots reconnus."
        save_studio(s)
        keep_audio = bool(align_after)

        if split_into_parts:
            stage_started = time.perf_counter()
            parts = build_story_parts(result, requested_part_count, duration)
            performance["episode_construction_seconds"] = time.perf_counter() - stage_started
            stage_started = time.perf_counter()
            sequences = detect_narrative_sequences(result)
            performance["narrative_detection_seconds"] = time.perf_counter() - stage_started
            result["performance"] = {key: round(value, 3) for key, value in performance.items()}
            result["narrative_diagnostics"] = {
                "method": "transcript lexical continuity, speech gaps and repeated openers; no calibrated probability",
                "sequences": sequences,
                "parts": [{
                    **part,
                    "start_boundary_evidence": next((seq.get("start_evidence") for seq in sequences
                                                      if abs(seq.get("start", -999) - part["start"]) < 0.01),
                                                     ["source edge or fallback boundary"]),
                    "end_boundary_evidence": next((seq.get("end_evidence") for seq in sequences
                                                    if abs(seq.get("end", -999) - part["end"]) < 0.01),
                                                   ["source edge or fallback boundary"]),
                    "boundary_confidence_label": part.get("boundary_confidence", "unavailable"),
                    "uncertainty": ("low transcript evidence" if part.get("boundary_confidence") == "low_transcript_evidence"
                                    else "heuristic boundary confidence; not statistically calibrated")
                } for part in parts],
            }
            s = load_studio()
            s["transcript"] = result
            s["parts"] = parts
            s["parts_count"] = requested_part_count
            s["scan_full"] = True
            s["analysis_mode"] = "story_parts"
            s["operation"] = "parts"
            s["status"] = "Découpage narratif terminé"
            s["message"] = f"{len(parts)} épisodes proposés. Les limites suivent des fins de phrases et des transitions narratives ; vérifie les coupures avant export."
            save_studio(s)
            save_transcript_snapshot(result)
            return

        if scan_for_candidates:
            result["alignment_status"] = "non effectué"
            result["alignment_audio"] = None
            result["alignment_message"] = "La transcription complète a servi au classement. Le réalignement sera fait sur le court extrait choisi."
            s = load_studio()
            s["transcript"] = result
            s["suggestions"] = suggestions
            s["scan_full"] = True
            s["status"] = "Analyse des extraits terminée"
            word_count = len(_normal_words(result))
            if suggestions:
                s["message"] = f"Analyse terminée : {len(suggestions)} passage(s) proposé(s) à partir de {word_count} mots reconnus. Prévisualise les vidéos et écoute chaque proposition avant de choisir."
            else:
                span = float(result.get("range_end", 0)) - float(result.get("range_start", 0))
                s["message"] = (f"Aucun passage suffisamment fiable n'a été retenu parmi {word_count} mots reconnus sur {span:.1f} s. "
                                "L'analyse a écarté les segments associés au silence, à la musique, aux répétitions ou à une reconnaissance vocale incertaine. "
                                "Essaie le modèle medium, ajoute les noms propres dans le vocabulaire, ou analyse une autre plage. "
                                "Tu peux aussi écouter la vidéo et sélectionner manuellement un passage.")
            save_studio(s)
            save_transcript_snapshot(result)
            return

        if align_after and aligner_python():
            s = load_studio()
            s["operation"] = "transcribe"
            s["status"] = "Réalignement acoustique…"
            s["message"] = "Transcription obtenue. Le modèle acoustique français recale maintenant les mots sur la voix."
            save_studio(s)
            try:
                result = run_acoustic_alignment(result, temp_audio)
                s = load_studio()
                s["transcript"] = result
                s["status"] = "Transcription et réalignement terminés"
                s["message"] = result.get("alignment_message", "Horodatages acoustiques calculés.")
                save_studio(s)
            except Exception as align_error:
                result["alignment_status"] = "échec"
                result["alignment_message"] = f"{type(align_error).__name__}: {align_error}"
                s = load_studio()
                s["transcript"] = result
                s["status"] = "Transcription terminée – réalignement à vérifier"
                s["message"] = "Le texte a été reconnu, mais le réalignement acoustique a échoué : " + result["alignment_message"]
                save_studio(s)
        elif align_after:
            s = load_studio()
            s["status"] = "Transcription terminée – moteur acoustique à installer"
            s["message"] = "Le texte est transcrit. Le clip utilisera les horaires Whisper en secours ; installe le moteur acoustique pour un réalignement automatique plus précis à l'export."
            save_studio(s)

        save_transcript_snapshot(result)
    except Exception as e:
        s = load_studio()
        s["status"] = "Erreur de transcription"
        s["message"] = f"{type(e).__name__}: {e}"
        save_studio(s)
    finally:
        if temp_audio and temp_audio.exists() and not keep_audio:
            try:
                temp_audio.unlink()
            except Exception:
                pass

def ass_time(seconds):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600); m = int((seconds % 3600) // 60); sec = seconds % 60
    return f"{h}:{m:02d}:{sec:05.2f}"

def clean_ass(text):
    return str(text).replace("\\", "／").replace("{", "(").replace("}", ")").replace("\n", " ").strip()

def _export_words_from_transcript(transcript, start, end):
    """Return absolute-timeline word cues, with safe fallbacks for legacy transcripts."""
    raw_words = transcript.get("words") or []
    if not raw_words:
        for seg in transcript.get("segments", []) or []:
            raw_words.extend(seg.get("words", []) or [])

    range_start = float(transcript.get("range_start", 0.0) or 0.0)
    range_end = float(transcript.get("range_end", range_start) or range_start)
    range_duration = max(0.0, range_end - range_start)

    # Legacy files sometimes store excerpt-relative timestamps. Detect that case.
    valid_times = []
    for item in raw_words:
        try:
            valid_times.append((float(item.get("start", 0)), float(item.get("end", 0))))
        except (TypeError, ValueError, AttributeError):
            continue
    offset = 0.0
    if valid_times and range_start > 30 and range_duration > 0:
        max_end = max(e for _, e in valid_times)
        min_start = min(s for s, _ in valid_times)
        if min_start >= -0.25 and max_end <= range_duration + 1.0:
            offset = range_start

    words = []
    for item in raw_words:
        try:
            token = str(item.get("word", "")).strip()
            w_start = float(item.get("start")) + offset
            w_end = float(item.get("end")) + offset
        except (TypeError, ValueError, AttributeError):
            continue
        if not token or w_end <= w_start:
            continue
        if w_end > start and w_start < end:
            words.append({"word": token, "start": w_start, "end": w_end})
    words.sort(key=lambda x: (x["start"], x["end"]))

    # Last-resort fallback: split segment text proportionally over its segment timing.
    # This guarantees captions when word-level timing is absent, while clearly
    # preferring word-level timestamps whenever they exist.
    if not words:
        for seg in transcript.get("segments", []) or []:
            try:
                seg_start = float(seg.get("start", 0)) + offset
                seg_end = float(seg.get("end", seg_start)) + offset
            except (TypeError, ValueError, AttributeError):
                continue
            text = str(seg.get("text", "") or "").strip()
            tokens = re.findall(r"\S+", text)
            if not tokens or seg_end <= seg_start or seg_end <= start or seg_start >= end:
                continue
            weights = [max(1, len(re.sub(r"[^\wÀ-ÿ]", "", t))) for t in tokens]
            total = sum(weights) or len(tokens)
            cursor = seg_start
            elapsed_weight = 0
            for i, (token, weight) in enumerate(zip(tokens, weights)):
                w_start = cursor
                elapsed_weight += weight
                w_end = seg_end if i == len(tokens) - 1 else seg_start + (seg_end - seg_start) * elapsed_weight / total
                if w_end > start and w_start < end:
                    words.append({"word": token, "start": w_start, "end": max(w_start + 0.03, w_end)})
                cursor = w_end
    return words


def build_ass(transcript, start, end, hook, path, subtitle_delay=0.0):
    """Build ASS subtitles using absolute source timestamps, converted to clip-relative time."""
    words = _export_words_from_transcript(transcript, start, end)

    groups, current, current_chars = [], [], 0
    for word in words:
        raw = clean_ass(word.get("word", ""))
        if not raw:
            continue
        token_len = len(raw.strip())
        pause = (float(word["start"]) - float(current[-1]["end"])) if current else 0.0
        previous_word = str(current[-1].get("word", "")).rstrip() if current else ""
        split_now = bool(current) and (
            pause > 0.42
            or len(current) >= 4
            or current_chars + token_len + 1 > 30
            or previous_word.endswith((".", "?", "!", ":", ";"))
        )
        if split_now:
            groups.append(current)
            current, current_chars = [], 0
        current.append(word)
        current_chars += token_len + 1
    if current:
        groups.append(current)

    lines = [
        "[Script Info]", "ScriptType: v4.00+", "WrapStyle: 2", "ScaledBorderAndShadow: yes",
        "PlayResX: 1080", "PlayResY: 1920", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Sub,Arial,54,&H00FFFFFF,&H00FFFFFF,&H90000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,1,2,72,72,340,1",
        "Style: Hook,Arial,62,&H00FFFFFF,&H00FFFFFF,&H90000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,2,8,70,70,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    clip_len = max(0.0, end - start)
    subtitle_count = 0
    if hook and str(hook).strip() and clip_len > 0:
        h = clean_ass(hook).replace("  ", " ")
        hook_end = min(2.0, clip_len)
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,{{\\an8\\pos(540,185)}}{h}")

    delay = float(subtitle_delay or 0.0)
    for group in groups:
        first_start = float(group[0]["start"])
        last_end = float(group[-1]["end"])
        cue_start = max(start, first_start + delay)
        cue_end = min(end, last_end + delay)
        if cue_end <= cue_start:
            continue
        # Professional emphasis: one genuinely meaningful term may grow very slightly, remaining white.
        emphasis_words = {"incroyable", "impossible", "jamais", "pourquoi", "comment", "choisis", "choisir", "meilleur", "meilleure", "pire", "secret", "attention", "regarde", "écoute", "attends", "personne", "énorme", "dingue", "horrible", "génial", "magnifique", "surprise", "surpris", "choqué", "choquée", "révélation", "catastrophe", "braquer", "banque", "million", "milliard"}
        neutral_words = {"tout", "tous", "toute", "toutes", "rien", "vraiment", "toujours", "beaucoup", "chose", "truc", "faire", "avoir", "être", "c'est", "mais", "donc", "alors", "comme", "vous", "nous", "elle", "elles", "ils", "leur", "leurs", "avec", "dans", "pour", "pas", "plus", "moins", "bien", "oui", "non"}
        def emphasis_rank(item):
            token = clean_ass(item.get("word", ""))
            bare = re.sub(r"^[^\wÀ-ÿ]+|[^\wÀ-ÿ]+$", "", token).lower()
            if not bare or bare in neutral_words:
                return 0
            rank = 0
            if bare in emphasis_words: rank += 6
            if re.search(r"\d", bare): rank += 3
            if len(bare) >= 9: rank += 2
            elif len(bare) >= 7: rank += 1
            if token.isupper() and len(token) >= 3: rank += 2
            if token.endswith(("!", "?")): rank += 1
            return rank
        ranked = [(emphasis_rank(w), idx) for idx, w in enumerate(group)]
        best_rank, best_idx = max(ranked, default=(0, -1))
        rendered = []
        for idx, item in enumerate(group):
            token = clean_ass(item.get("word", ""))
            if idx == best_idx and best_rank >= 5:
                rendered.append("{\\b1\\fscx100\\fscy100\\t(0,120,\\fscx112\\fscy112)}" + token + "{\\rSub}")
            else:
                rendered.append(token)
        caption = " ".join(rendered).strip()
        caption = re.sub(r"\s+([,.;:!?])", r"\1", caption)
        if caption:
            # ASS events are clip-relative. The video input seek is also reset to zero
            # before filtering in make_clip_worker.
            local_start = max(0.0, cue_start - start)
            local_end = max(local_start + 0.04, min(clip_len, cue_end - start))
            lines.append(
                f"Dialogue: 0,{ass_time(local_start)},{ass_time(local_end)},Sub,,0,0,0,,{{\\an2\\pos(540,1550)\\q2}}{caption}"
            )
            subtitle_count += 1

    if subtitle_count == 0:
        raise RuntimeError(
            "Aucun sous-titre n'a pu être généré pour cet extrait. "
            "La transcription est conservée : vérifie ses timestamps et la plage du clip."
        )
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return subtitle_count

def align_transcript_for_export(transcript, source, status_callback=None):
    """Try acoustic alignment immediately before export; always preserve Whisper fallback timings."""
    current_status = str(transcript.get("alignment_status", "") or "")
    if current_status == "acoustique" and transcript.get("words"):
        return transcript, "Horodatages acoustiques déjà disponibles."
    if not aligner_python():
        transcript["alignment_status"] = "secours_whisper"
        transcript["alignment_message"] = "Moteur acoustique indisponible : horodatages Whisper utilisés."
        return transcript, transcript["alignment_message"]
    try:
        if status_callback:
            status_callback("Réalignement acoustique automatique…", "Recalage mot par mot avant l'export du clip…")
        audio_path = ensure_alignment_audio(transcript, source)
        aligned = run_acoustic_alignment(transcript, audio_path)
        aligned["alignment_status"] = "acoustique"
        aligned["alignment_error"] = ""
        return aligned, aligned.get("alignment_message", "Réalignement acoustique terminé.")
    except Exception as exc:
        # Do not drop the transcript or block the export if the aligner fails.
        fallback = json.loads(json.dumps(transcript, ensure_ascii=False))
        fallback["alignment_status"] = "secours_whisper"
        fallback["alignment_message"] = f"Réalignement indisponible ({type(exc).__name__}: {exc}). Horodatages Whisper conservés."
        return fallback, fallback["alignment_message"]


def make_clip_worker(start, end, hook, title, subtitle_delay=0.0, framing="auto"):
    s = load_studio()
    try:
        source = Path(s["source"])
        transcript = s.get("transcript") or {}
        if not transcript.get("segments"):
            raise RuntimeError("La transcription est absente. Transcris d'abord l'extrait sélectionné.")
        def set_clip_status(status, message):
            current = load_studio()
            current["operation"] = "clip"
            current["status"] = status
            current["message"] = message
            save_studio(current)
        if transcript.get("alignment_status") != "acoustique" or not transcript.get("words"):
            transcript, align_message = align_transcript_for_export(transcript, source, set_clip_status)
            s = load_studio()
            s["transcript"] = transcript
            s["operation"] = "clip"
            s["message"] = align_message
            s["status"] = "Création du clip…"
            save_studio(s)
            save_transcript_snapshot(transcript)
        set_clip_status("Création du clip…", "Réalignement terminé ou secours Whisper validé. Assemblage vidéo vertical, sous-titres et audio…")
        if framing == "auto":
            selected_framing, framing_reason = detect_framing(source, start, end)
        elif framing in ("blur", "crop"):
            selected_framing, framing_reason = framing, "choix manuel"
        else:
            raise ValueError("Style de cadrage inconnu.")
        d = load_data()
        title = unique_clip_title(title, s, d)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_")[:35] or "clip"
        filename = f"{safe_title}_{stamp}.mp4"
        output = EXPORTS / filename
        suffix = 2
        while output.exists():
            filename = f"{safe_title}_{stamp}_{suffix}.mp4"
            output = EXPORTS / filename
            suffix += 1
        ass_path = EXPORTS / f"{safe_title}_{stamp}.ass"
        subtitle_count = build_ass(transcript, start, end, hook, ass_path, subtitle_delay=subtitle_delay)
        if subtitle_count <= 0:
            raise RuntimeError("Export annulé : aucun sous-titre exploitable n’a été généré.")
        ffmpeg = ffmpeg_exe()
        if selected_framing == "crop":
            source_w, source_h = get_video_size(source)
            crop_center = 0.5
            # Face-centred crop when the optional OpenCV detector is available.
            try:
                import cv2
                cap = cv2.VideoCapture(str(source))
                face_cascade = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
                centres=[]
                for sample_t in [start+(end-start)*k/5 for k in (1,2,3,4)]:
                    cap.set(cv2.CAP_PROP_POS_MSEC, sample_t*1000)
                    ok, frame = cap.read()
                    if not ok: continue
                    h0,w0=frame.shape[:2]
                    sc=min(640/max(1,w0),1.0)
                    if sc<1: frame=cv2.resize(frame,(int(w0*sc),int(h0*sc)))
                    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
                    faces=face_cascade.detectMultiScale(gray,1.1,5,minSize=(22,22)) if not face_cascade.empty() else []
                    for (x,y,w,h) in faces: centres.append((x+w/2)/gray.shape[1])
                cap.release()
                if centres: crop_center=max(0.0,min(1.0,sum(centres)/len(centres)))
            except Exception:
                pass
            vf=("scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop=1080:1920:x='max(0,min(iw-ow,iw*{crop_center:.4f}-ow/2))':y=0,setsar=1,ass={ass_path.name}[v]")
        else:
            # Preserve the whole source image; a blurred duplicate fills the unused canvas.
            vf = ("[0:v:0]split=2[bgsrc][fgsrc];"
                  "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,boxblur=24:10,eq=brightness=-0.17:saturation=0.82[bg];"
                  "[fgsrc]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                  f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass={ass_path.name}[v]")
        cmd = [ffmpeg, "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end-start:.3f}", "-filter_complex", vf,
               "-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)]
        result = subprocess.run(cmd, cwd=str(EXPORTS), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200)
        if result.returncode != 0:
            raise RuntimeError("FFmpeg n'a pas pu créer le clip : " + result.stderr[-1800:])
        d = load_data()
        campaign_id = str(s.get("campaign_id") or "")
        campaign = next((item for item in d.get("campaigns", []) if str(item.get("id", "")) == campaign_id), None)
        d["clips"].append({"id": str(uuid.uuid4())[:8], "title": title, "account": "À définir", "campaign_id": campaign_id,
                            "campaign": campaign.get("name", "") if campaign else "", "status": "À publier", "url": "",
                            "eligible_views": 0, "earnings": 0, "cost": 0, "created": datetime.date.today().isoformat(), "local_file": str(output)})
        save_data(d)
        s = load_studio(); s["operation"] = "clip"; s["status"] = "Clip prêt"; s["message"] = f"Clip exporté : {filename} · cadrage : {'plein écran' if selected_framing == 'crop' else 'image entière + fond flou'} ({framing_reason})"; s["last_output"] = str(output); save_studio(s)
        try: ass_path.unlink()
        except Exception: pass
    except Exception as e:
        s = load_studio(); s["operation"] = "clip"; s["status"] = "Erreur de création"; s["message"] = f"{type(e).__name__}: {e}"; save_studio(s)

class Handler(BaseHTTPRequestHandler):

    def _serve_stream_file(self, target, content_type=None):
        """Serve a local media file with byte-range support for the HTML5 video preview."""
        target = Path(target)
        if not target.exists() or not target.is_file():
            self.respond("Fichier introuvable", 404)
            return
        size = target.stat().st_size
        ctype = content_type or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        range_header = self.headers.get("Range", "")
        start, end, status_code = 0, max(0, size - 1), 200
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
            if not match:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
            left, right = match.groups()
            if left == "":
                suffix = int(right or "0")
                if suffix <= 0:
                    self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
                start = max(0, size - suffix)
            else:
                start = int(left)
            if right and left:
                end = min(size - 1, int(right))
            if start >= size or end < start:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
            status_code = 206
        length = max(0, end - start + 1)
        self.send_response(status_code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache")
        if status_code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with target.open("rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def log_message(self, *_): pass
    def respond(self, content, code=200, ctype="text/html; charset=utf-8", headers=None):
        b = content if isinstance(content, bytes) else str(content).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        sid = (query.get("sid", ["default"])[0] or "default")
        ACTIVE_SESSION_ID.set(sid if re.fullmatch(r"[A-Za-z0-9_-]{1,48}", sid) else "default")
        d = load_data()
        if path == "/": self.respond(dashboard())
        elif path == "/library": self.respond(library_page())
        elif path == "/studio":
            if query.get("new", ["0"])[0] == "1":
                new_sid = create_session()
                self.send_response(303); self.send_header("Location", f"/studio?sid={new_sid}"); self.end_headers(); return
            close_id = query.get("close", [""])[0]
            if close_id:
                idx = load_sessions_index()
                opened = [x for x in idx["sessions"] if x.get("open") and x.get("id") != close_id]
                if len(opened) >= 1:
                    close_session_tab(close_id)
                    active_sid = query.get("sid", ["default"])[0]
                    if active_sid == close_id: active_sid = opened[-1]["id"]
                    self.send_response(303); self.send_header("Location", f"/studio?sid={active_sid}"); self.end_headers(); return
                self.respond(page("<div class='card'><h2>Impossible de fermer le dernier onglet</h2><a class='btn' href='/studio?sid=" + esc(close_id) + "'>Revenir à la session</a></div>", "Clip Factory")); return
            if query.get("reopen", ["0"])[0] == "1":
                open_session_tab(sid)
            # A direct visit to /studio opens the default session; it never wipes it.
            ACTIVE_SESSION_ID.set(sid)
            self.respond(studio_page())
        elif path == "/status":
            st = load_studio()
            self.respond(json.dumps({"status": st.get("status", ""), "message": st.get("message", ""), "operation": st.get("operation", "transcribe"), "busy": session_worker_active(sid)}, ensure_ascii=False), ctype="application/json; charset=utf-8", headers={"Cache-Control":"no-store"})
        elif path == "/reference-review":
            self.respond(reference_review_page())
        elif path == "/source-preview":
            st = load_studio()
            source = st.get("source")
            if not source or not Path(source).exists():
                self.respond("Aucune vidéo source chargée", 404)
                return
            self._serve_stream_file(source, mimetypes.guess_type(str(source))[0] or "video/mp4")
        elif path == "/export": self.respond(json.dumps(d, ensure_ascii=False, indent=2), ctype="application/json; charset=utf-8")
        elif path == "/csv":
            import csv, io
            out = io.StringIO(); w = csv.writer(out, delimiter=";"); w.writerow(["Titre","Compte","Campagne","Statut","Vues eligibles","Gains EUR","Cout EUR","URL"])
            for c in d["clips"]: w.writerow([c.get("title"),c.get("account"),c.get("campaign"),c.get("status"),c.get("eligible_views"),c.get("earnings"),c.get("cost"),c.get("url")])
            self.respond(out.getvalue(), ctype="text/csv; charset=utf-8")
        elif path.startswith("/files/"):
            name = Path(unquote(path[len("/files/"):])).name
            target = EXPORTS / name
            if not target.exists() or target.suffix.lower() != ".mp4": self.respond("Fichier introuvable",404); return
            self.respond(target.read_bytes(), ctype="video/mp4", headers={"Content-Disposition": f'attachment; filename="{name}"'})
        else: self.respond("Introuvable",404)
    def do_HEAD(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        sid = parse_qs(parsed_url.query).get("sid", ["default"])[0]
        ACTIVE_SESSION_ID.set(sid if re.fullmatch(r"[A-Za-z0-9_-]{1,48}", sid) else "default")
        if path == "/source-preview":
            st = load_studio(); source = st.get("source")
            if not source or not Path(source).exists():
                self.send_response(404); self.end_headers(); return
            target = Path(source); size = target.stat().st_size
            self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "video/mp4")
            self.send_header("Content-Length", str(size)); self.send_header("Accept-Ranges", "bytes"); self.end_headers(); return
        self.send_response(404); self.end_headers()
    def do_POST(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        sid = parse_qs(parsed_url.query).get("sid", ["default"])[0]
        ACTIVE_SESSION_ID.set(sid if re.fullmatch(r"[A-Za-z0-9_-]{1,48}", sid) else "default")
        if path == "/upload":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_UPLOAD: raise ValueError("Fichier vide ou supérieur à 3 Go.")
                ctype = self.headers.get("Content-Type", "")
                m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
                if not m: raise ValueError("Formulaire d'envoi non reconnu.")
                boundary = (m.group(1) or m.group(2)).encode("ascii")
                first_raw = self.rfile.readline()
                first = first_raw.rstrip(b"\r\n")
                if first != b"--" + boundary: raise ValueError("Début du fichier non reconnu.")
                consumed = len(first_raw)
                headers = {}
                while True:
                    line = self.rfile.readline()
                    consumed += len(line)
                    if line in (b"\r\n", b"\n", b""): break
                    try:
                        k,v = line.decode("utf-8", "replace").split(":",1); headers[k.lower().strip()] = v.strip()
                    except ValueError: pass
                disp = headers.get("content-disposition", "")
                fm = re.search(r'filename="([^"]*)"', disp)
                original = Path(fm.group(1)).name if fm else "source.mp4"
                ext = Path(original).suffix.lower()
                if ext not in (".mp4", ".mov", ".mkv", ".webm", ".m4v"): raise ValueError("Format accepté : MP4, MOV, MKV, WEBM ou M4V.")
                marker = b"\r\n--" + boundary
                remaining = length - consumed
                if remaining <= 0: raise ValueError("Le fichier transmis est vide.")
                uid = uuid.uuid4().hex[:10]
                target = SOURCES / f"source_{uid}{ext}"
                buf = b""; total = 0; found = False
                with target.open("wb") as out:
                    while total < remaining:
                        chunk = self.rfile.read(min(1024*1024, remaining-total))
                        if not chunk: break
                        total += len(chunk); buf += chunk
                        idx = buf.find(marker)
                        if idx >= 0:
                            out.write(buf[:idx]); found = True; break
                        keep = len(marker) + 4
                        if len(buf) > keep:
                            out.write(buf[:-keep]); buf = buf[-keep:]
                    if not found: raise ValueError("Envoi interrompu : fichier incomplet.")
                duration = get_duration(target)
                width, height = get_video_size(target)
                s = load_studio()
                s.update({"source": str(target), "duration": duration, "width": width, "height": height,
                          "selection_start": 0.0,
                          "selection_end": min(duration, 45.0),
                          "status": "Vidéo chargée – transcription à lancer",
                          "message": f"{original} · {target.stat().st_size/1024/1024:.1f} Mo · {width} × {height}",
                          "transcript": None, "last_output": None, "suggestions": [], "scan_full": False, "parts": [], "parts_count": 4, "analysis_mode": "highlights", "operation": "transcribe"})
                save_studio(s)
                update_session_label(ACTIVE_SESSION_ID.get(), Path(original).stem[:48])
                self.close_connection = True
                self.send_response(303); self.send_header("Location", f"/studio?sid={ACTIVE_SESSION_ID.get()}#source-card"); self.end_headers()
            except Exception as e:
                self.respond(page(f"<div class='card'><h2>Erreur lors du chargement</h2><p>{esc(e)}</p><a class='btn' href='/studio'>Retour à l'atelier</a></div>","Atelier vidéo"),400)
            return
        length = int(self.headers.get("Content-Length",0))
        raw = self.rfile.read(length).decode("utf-8", "replace")
        form = parse_qs(raw, keep_blank_values=True)
        val = lambda k, default="": form.get(k,[default])[0].strip()
        if path == "/workflow-mode":
            st = load_studio()
            mode = val("mode", "highlights")
            if mode not in ("highlights", "story_parts"):
                mode = "highlights"
            st["analysis_mode"] = mode
            st["suggestions"] = []
            st["parts"] = []
            st["message"] = "Mode sélectionné : " + ("meilleurs extraits indépendants" if mode == "highlights" else "épisodes narratifs successifs")
            st["status"] = "Mode de traitement sélectionné"
            save_studio(st)
        elif path == "/split-parts":
            st = load_studio()
            try:
                if not st.get("source"):
                    raise ValueError("Charge d'abord une vidéo source.")
                count = int(val("count", "4"))
                if count < 2 or count > 30:
                    raise ValueError("Choisis entre 2 et 30 épisodes.")
                duration = float(st.get("duration", 0) or 0)
                if duration < 60:
                    raise ValueError("La vidéo est trop courte pour être découpée en épisodes successifs.")
                st["analysis_mode"] = "story_parts"
                st["parts_count"] = count
                st["parts"] = []
                st["suggestions"] = []
                existing = st.get("transcript") or {}
                full_transcript_ready = (existing.get("segments")
                    and float(existing.get("range_start", 999999)) <= 0.5
                    and float(existing.get("range_end", 0)) >= duration - 1.0)
                if full_transcript_ready:
                    parts = build_story_parts(existing, count, duration)
                    st["parts"] = parts
                    st["scan_full"] = True
                    st["operation"] = "parts"
                    st["status"] = "Découpage narratif terminé"
                    st["message"] = f"{len(parts)} épisodes proposés à partir de la transcription existante."
                    save_studio(st)
                else:
                    if session_worker_active():
                        raise ValueError("Un traitement est déjà en cours. Attends sa fin.")
                    st["transcript"] = None
                    st["scan_full"] = True
                    st["selection_start"] = 0.0
                    st["selection_end"] = duration
                    st["operation"] = "parts"
                    st["status"] = "Transcription complète pour construire les épisodes…"
                    st["message"] = f"Transcription de toute la vidéo ({duration/60:.1f} minutes), puis recherche de {count} coupures narratives."
                    save_studio(st)
                    st["transcription_prompt"] = val("vocabulary", st.get("transcription_prompt", ""))
                    save_studio(st)
                    spawn_session_worker(transcribe_worker, ("small", 0.0, duration, False, False, True, count, st["transcription_prompt"]))
            except Exception as e:
                st = load_studio()
                st["operation"] = "parts"
                st["status"] = "Erreur de découpage narratif"
                st["message"] = str(e)
                save_studio(st)
        elif path == "/select-part":
            st = load_studio()
            try:
                part_start = parse_user_float(val("start"), "Début de partie"); part_end = parse_user_float(val("end"), "Fin de partie")
                full_duration = float(st.get("duration", 0) or 0)
                if not st.get("source") or part_start < 0 or part_end <= part_start or part_end > full_duration + 0.1:
                    raise ValueError("Partie vidéo invalide.")
                st["selection_start"] = part_start
                st["selection_end"] = part_end
                st["transcript"] = None
                st["scan_full"] = False
                st["suggestions"] = []
                st["operation"] = "transcribe"
                st["status"] = "Partie sélectionnée"
                st["message"] = f"Partie sélectionnée : {part_start:.1f} à {part_end:.1f} secondes. Tu peux maintenant la transcrire."
                save_studio(st)
            except Exception as e:
                st["status"] = "Erreur de sélection de partie"
                st["message"] = str(e)
                save_studio(st)
        elif path == "/campaign":
            d=load_data()
            if val("name"):
                campaign_min = int(val("min_duration", "20") or 20)
                campaign_max = int(val("max_duration", "45") or 45)
                if campaign_min < 3 or campaign_max < campaign_min or campaign_max > 300:
                    raise ValueError("Vérifie les durées de campagne (3 à 300 secondes, minimum ≤ maximum).")
                d["campaigns"].append({"id":str(uuid.uuid4())[:8],"name":val("name"),"platform":val("platform"),"niche":val("niche"),"deadline":val("deadline"),"min_duration":campaign_min,"max_duration":campaign_max,"rate":parse_user_float(val("rate") or "0", "Rémunération"),"budget":parse_user_float(val("budget") or "0", "Budget"),"rules":val("rules"),"status":"Active"}); save_data(d)
        elif path == "/clip":
            d=load_data()
            if val("title"):
                d["clips"].append({"id":str(uuid.uuid4())[:8],"title":val("title"),"account":val("account"),"campaign":val("campaign"),"status":val("status"),"url":val("url"),"eligible_views":int(val("views") or 0),"earnings":float(val("earnings") or 0),"cost":float(val("cost") or 0),"created":datetime.date.today().isoformat()}); save_data(d)
        elif path == "/settings":
            d=load_data(); d["settings"]["monthly_goal"]=parse_user_float(val("goal") or "500", "Objectif mensuel"); save_data(d)
        elif path == "/save-transcript":
            s = load_studio()
            transcript = s.get("transcript")
            if transcript and isinstance(transcript.get("segments"), list):
                segments = transcript["segments"]
                updated_words = []
                changed = 0
                for i, seg in enumerate(segments):
                    key = f"seg_{i}"
                    if key not in form:
                        seg_words = seg.get("words", [])
                        updated_words.extend(seg_words)
                        continue
                    new_text = form.get(key, [seg.get("text", "")])[0].strip()
                    old_text = seg.get("text", "")
                    if new_text != old_text:
                        changed += 1
                    seg["text"] = new_text
                    tokens = re.findall(r"\S+", new_text)
                    old_words = list(seg.get("words", []))
                    if not tokens:
                        seg["words"] = []
                    elif len(tokens) == len(old_words) and old_words:
                        # Keep Whisper's original word timings when only spelling/punctuation changes.
                        for item, token in zip(old_words, tokens):
                            item["word"] = token
                        seg["words"] = old_words
                    else:
                        # Preserve timestamps for words that still match. Only newly inserted or
                        # replaced tokens receive interpolated times between their nearest anchors.
                        start_t = float(seg.get("start", 0.0))
                        end_t = max(start_t + 0.05, float(seg.get("end", start_t + 0.5)))
                        def norm_token(value):
                            return re.sub(r"[^\wÀ-ÿ]", "", str(value).lower(), flags=re.UNICODE)
                        old_norm = [norm_token(x.get("word", "")) for x in old_words]
                        new_norm = [norm_token(x) for x in tokens]
                        mapped = {}
                        matcher = difflib.SequenceMatcher(a=old_norm, b=new_norm, autojunk=False)
                        for block in matcher.get_matching_blocks():
                            for offset in range(block.size):
                                mapped[block.b + offset] = block.a + offset
                        rebuilt = [None] * len(tokens)
                        for new_i, old_i in mapped.items():
                            original = old_words[old_i]
                            rebuilt[new_i] = {"start": float(original.get("start", start_t)),
                                              "end": float(original.get("end", end_t)),
                                              "word": tokens[new_i]}
                        i = 0
                        while i < len(tokens):
                            if rebuilt[i] is not None:
                                i += 1
                                continue
                            j = i
                            while j < len(tokens) and rebuilt[j] is None:
                                j += 1
                            left = float(rebuilt[i-1]["end"]) if i > 0 and rebuilt[i-1] else start_t
                            right = float(rebuilt[j]["start"]) if j < len(tokens) and rebuilt[j] else end_t
                            if right < left:
                                right = left
                            weights = [max(1, len(tokens[k].strip(".,!?;:()[]{}«»\"'"))) for k in range(i, j)]
                            total_weight = sum(weights) or (j-i)
                            cursor = left
                            for k, weight in zip(range(i, j), weights):
                                word_start = cursor
                                word_end = right if k == j-1 else left + (right-left) * (sum(weights[:k-i+1]) / total_weight)
                                rebuilt[k] = {"start": word_start, "end": max(word_start + 0.01, word_end), "word": tokens[k]}
                                cursor = word_end
                            i = j
                        seg["words"] = rebuilt
                    updated_words.extend(seg.get("words", []))
                transcript["words"] = updated_words
                if changed:
                    transcript["alignment_status"] = "à refaire"
                    transcript["alignment_message"] = "Le texte a changé. Réalignement acoustique automatique prévu au prochain export."
                s["transcript"] = transcript
                s["edit_message"] = f"Corrections enregistrées : {changed} segment(s) modifié(s)."
                s["status"] = "Transcription corrigée"
                s["message"] = "Les modifications sont enregistrées. Les mots seront recalés automatiquement lors de la prochaine création du clip."
                save_studio(s)
                save_transcript_snapshot(transcript)
            else:
                s["edit_message"] = "Aucune transcription à corriger."
                save_studio(s)
        elif path == "/annotate-candidate":
            s = load_studio()
            try:
                source = s.get("source") or ""
                candidates = s.get("suggestions", []) or []
                key = val("candidate_id")
                candidate = next((c for c in candidates if candidate_id(source, c) == key), None)
                if candidate is None:
                    raise ValueError("Cette proposition n'est plus disponible. Relance l'analyse avant de l'évaluer.")
                save_annotation(source, candidate, val("rating"), form.get("reason", []), val("comment"),
                                source_sha256=source_fingerprint(source))
                s["message"] = "Évaluation humaine enregistrée dans le jeu de validation. Le moteur ne s'entraîne pas sur ces annotations."
                save_studio(s)
            except Exception as e:
                s["status"] = "Évaluation non enregistrée"
                s["message"] = str(e)
                save_studio(s)
        elif path == "/annotate-missed":
            s = load_studio()
            try:
                source = s.get("source")
                if not source:
                    raise ValueError("Aucune vidéo source n'est chargée dans cette session.")
                start = parse_user_float(val("start"), "Début du passage")
                end = parse_user_float(val("end"), "Fin du passage")
                duration = float(s.get("duration", 0) or 0)
                if start < 0 or end <= start or end > duration + 0.05:
                    raise ValueError("Les temps du passage dépassent la vidéo ou sont invalides.")
                content_type = val("content_type")
                source_sha256 = source_fingerprint(source)
                register_source(source, source_sha256, content_type, duration, video_path=source)
                save_reference_span(source, source_sha256, start, end,
                                    val("narrative_interest"), val("justification"), content_type)
                s["status"] = "Passage ajouté au jeu de référence"
                s["message"] = "Le passage et son intérêt narratif sont enregistrés séparément de la transcription et des résultats du moteur."
                save_studio(s)
            except Exception as e:
                s["status"] = "Passage de référence non enregistré"
                s["message"] = str(e)
                save_studio(s)
        elif path == "/realign":
            s = load_studio()
            try:
                if not s.get("transcript"):
                    raise ValueError("Transcris d'abord un extrait.")
                if session_worker_active():
                    raise ValueError("Un traitement est déjà en cours. Attends sa fin.")
                if not aligner_python():
                    raise ValueError("Le moteur acoustique n'est pas installé. Ferme Clip Factory et lance INSTALLER_ALIGNEMENT.bat.")
                s["operation"] = "align"
                s["status"] = "Réalignement acoustique…"
                s["message"] = "Le texte actuel va être réaligné sur l'audio d'origine."
                save_studio(s)
                spawn_session_worker(realign_worker)
            except Exception as e:
                s["operation"] = "align"
                s["status"] = "Erreur de réalignement"
                s["message"] = str(e)
                save_studio(s)
        elif path == "/find-candidates":
            s = load_studio()
            try:
                if not s.get("source"):
                    raise ValueError("Charge d'abord une vidéo source.")
                if session_worker_active():
                    raise ValueError("Un traitement est déjà en cours. Attends sa fin.")
                duration = float(s.get("duration", 0) or 0)
                scan_start = parse_user_float(val("start", "0"), "Début")
                scan_end = parse_user_float(val("end", str(duration)), "Fin")
                if scan_start < 0 or scan_end <= scan_start or scan_end > duration + 0.1:
                    raise ValueError("Plage de partie invalide.")
                data = load_data()
                campaign_id = val("campaign_id", s.get("campaign_id", ""))
                campaign = next((c for c in data.get("campaigns", []) if str(c.get("id", "")) == campaign_id), None)
                if campaign_id and campaign is None:
                    raise ValueError("La campagne sélectionnée n'existe plus. Recharge l'atelier.")
                candidate_min = float((campaign or {}).get("min_duration", 20) or 20)
                candidate_max = float((campaign or {}).get("max_duration", 45) or 45)
                candidate_limit = int(val("candidate_limit", "8") or 8)
                if candidate_min < 3 or candidate_max < candidate_min or candidate_max > 300:
                    raise ValueError("Les durées de la campagne sont invalides.")
                if candidate_limit < 1 or candidate_limit > 30:
                    raise ValueError("Le nombre maximal de propositions doit être compris entre 1 et 30.")
                if scan_end - scan_start < candidate_min:
                    raise ValueError(f"La partie doit durer au moins {candidate_min:.0f} secondes pour cette campagne.")
                model = val("model", "small")
                if model not in ("base", "small", "medium"):
                    model = "small"
                s["analysis_mode"] = "highlights"
                s["campaign_id"] = campaign_id
                s["candidate_min_duration"] = candidate_min
                s["candidate_max_duration"] = candidate_max
                s["candidate_limit"] = candidate_limit
                s["parts"] = []
                s["selection_start"] = scan_start
                s["selection_end"] = scan_end
                s["transcript"] = None
                s["suggestions"] = []
                s["scan_full"] = True
                s["operation"] = "candidates"
                s["status"] = "Transcription complète pour détecter les extraits…"
                s["message"] = f"Analyse de la partie ({scan_start:.1f} à {scan_end:.1f} s) pour chercher des passages de {candidate_min:.0f} à {candidate_max:.0f} secondes."
                save_studio(s)
                s["transcription_prompt"] = val("vocabulary", s.get("transcription_prompt", ""))
                save_studio(s)
                spawn_session_worker(transcribe_worker, (model, scan_start, scan_end, False, True, False, 4, s["transcription_prompt"], candidate_min, candidate_max, candidate_limit))
            except Exception as e:
                s["status"] = "Erreur d'analyse"
                s["message"] = str(e)
                save_studio(s)
        elif path == "/transcribe":
            s = load_studio()
            try:
                if not s.get("source"):
                    raise ValueError("Charge d'abord une vidéo source.")
                if session_worker_active():
                    raise ValueError("Un traitement est déjà en cours. Attends sa fin avant d'en lancer un autre.")
                selected_start = parse_user_float(val("start", "0"), "Début de l’extrait")
                selected_end = parse_user_float(val("end", "0"), "Fin de l’extrait")
                duration = float(s.get("duration", 0) or 0)
                if selected_start < 0 or selected_end <= selected_start:
                    raise ValueError("Vérifie le début et la fin de l'extrait.")
                if selected_end > duration + 0.1:
                    raise ValueError(f"La fin dépasse la durée de la vidéo ({duration:.1f} secondes).")
                if selected_end - selected_start < 3:
                    raise ValueError("Choisis au moins 3 secondes à transcrire.")
                model = val("model", "small")
                if model not in ("base", "small", "medium"):
                    model = "small"
                s["selection_start"] = selected_start
                s["selection_end"] = selected_end
                s["transcription_prompt"] = val("vocabulary", s.get("transcription_prompt", ""))
                s["suggestions"] = []
                s["transcript"] = None
                s["scan_full"] = False
                s["operation"] = "transcribe"
                s["status"] = "Extraction de l'extrait audio…"
                s["message"] = f"La transcription sera limitée à {selected_start:.1f}–{selected_end:.1f} secondes."
                save_studio(s)
                spawn_session_worker(transcribe_worker, (model, selected_start, selected_end, True, False, False, 4, s["transcription_prompt"]))
            except Exception as e:
                s["operation"] = "transcribe"
                s["status"] = "Erreur de sélection"
                s["message"] = str(e)
                save_studio(s)
        elif path == "/make-clip":
            s=load_studio()
            try:
                if session_worker_active():
                    raise ValueError("Un traitement est déjà en cours. Attends sa fin avant de créer un autre clip.")
                start=parse_user_float(val("start"), "Début du clip"); end=parse_user_float(val("end"), "Fin du clip"); hook=val("hook"); title=val("title", ""); subtitle_delay=parse_user_float(val("subtitle_delay", "0"), "Décalage des sous-titres"); framing=val("framing", "auto")
                if not s.get("source") or not s.get("transcript"): raise ValueError("Charge et transcris d'abord une vidéo.")
                if start < 0 or end <= start or end-start < 5: raise ValueError("Vérifie les temps de début et de fin.")
                selected_campaign_id = val("campaign_id", "")
                selected_campaign = next((c for c in load_data().get("campaigns", []) if str(c.get("id", "")) == selected_campaign_id), None)
                if selected_campaign_id and selected_campaign is None:
                    raise ValueError("La campagne sélectionnée n'existe plus. Recharge l'atelier.")
                if selected_campaign:
                    min_allowed = float(selected_campaign.get("min_duration", 20) or 20)
                    max_allowed = float(selected_campaign.get("max_duration", 45) or 45)
                    if not min_allowed <= end - start <= max_allowed:
                        raise ValueError(f"Durée non conforme à la campagne : {min_allowed:.0f}–{max_allowed:.0f} secondes.")
                elif end-start > 60: raise ValueError("Sans campagne, la limite locale est de 60 secondes par clip.")
                if end > float(s.get("duration",0)): raise ValueError("La fin dépasse la durée de la vidéo.")
                if not -2.0 <= subtitle_delay <= 2.0: raise ValueError("Le décalage des sous-titres doit être compris entre -2,00 et +2,00 secondes.")
                transcript_range_start = float(s["transcript"].get("range_start", 0))
                transcript_range_end = float(s["transcript"].get("range_end", s.get("duration", 0)))
                if start < transcript_range_start - 0.05 or end > transcript_range_end + 0.05:
                    raise ValueError(f"Tu as transcrit uniquement {transcript_range_start:.1f} à {transcript_range_end:.1f} secondes. Le clip doit rester dans cette plage. Relance la transcription si tu veux un autre passage.")
                if framing not in ("auto", "blur", "crop"): raise ValueError("Choisis un style de cadrage valide.")
                s["campaign_id"] = selected_campaign_id
                s["operation"]="clip"; s["status"]="Création du clip…"; s["message"]="Analyse du cadrage et préparation de l'export."; save_studio(s)
                spawn_session_worker(make_clip_worker, (start,end,hook,title,subtitle_delay,framing))
            except Exception as e:
                s["operation"]="clip"; s["status"]="Erreur de création"; s["message"]=str(e); save_studio(s)
        target = f"/studio?sid={sid}"
        if path == "/annotate-missed": target = f"/reference-review?sid={sid}#reference-review"
        if path in ("/split-parts", "/workflow-mode"): target += "#workflow-card"
        elif path == "/select-part": target += "#transcribe-card"
        elif path == "/make-clip": target += "#clip-card"
        elif path == "/save-transcript": target += "#transcript-card"
        elif path == "/annotate-candidate": target += "#candidate-results"
        elif path == "/annotate-missed": pass
        self.send_response(303)
        self.send_header("Location", target if path in ("/upload", "/transcribe", "/find-candidates", "/make-clip", "/save-transcript", "/realign", "/split-parts", "/select-part", "/workflow-mode", "/annotate-candidate", "/annotate-missed") else "/")
        self.end_headers()

def main():
    load_data(); load_studio()
    url=f"http://{HOST}:{PORT}"
    print("Clip Factory est lancé :",url)
    print("Garde cette fenêtre ouverte. Ferme-la pour arrêter le serveur.")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()

if __name__ == "__main__": main()
