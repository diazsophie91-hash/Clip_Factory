"""Real end-to-end validation of the narrative-episode workflow.

Drives the real HTTP handler (multipart upload, Whisper, optional no-VAD recovery,
sequence detection, episode building, acoustic alignment, FFmpeg export) on the
original source videos. Every mutable path of ``app`` is redirected to a temporary
validation directory: existing sessions, media, exports and data are never touched,
and the source videos are only read (streamed through the upload route).

Usage (from the project root, with the runtime interpreter):
    .venv\\Scripts\\python.exe -m tools.real_e2e_validation --out <dir> [--export-all]
"""
import argparse
import json
import mimetypes
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import app

SOURCES = {
    "voici_3_mondes": app.ROOT / "media" / "sources" / "source_57e0fbd52b.mp4",
    "les_pubs_vs_la_vie": app.ROOT / "media" / "sources" / "source_5efb9fef42.mp4",
}


def isolate(out_dir):
    """Redirect every mutable path of the application to ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sessions = out_dir / "sessions"
    media = out_dir / "media"
    sources, exports = media / "sources", media / "exports"
    for folder in (sessions, sources, exports):
        folder.mkdir(parents=True, exist_ok=True)
    app.DATA = out_dir / "data.json"
    app.STUDIO = out_dir / "studio.json"
    app.SESSIONS_DIR = sessions
    app.SESSIONS_INDEX = sessions / "index.json"
    app.MEDIA, app.SOURCES, app.EXPORTS = media, sources, exports
    app.save_data({"settings": {"monthly_goal": 0, "currency": "EUR"},
                   "campaigns": [{"id": "validation", "name": "Validation temporaire",
                                  "min_duration": 3, "max_duration": 300, "status": "Active"}],
                   "clips": []})


def post_form(base, path, sid, fields):
    body = "&".join(f"{k}={v}" for k, v in fields.items()).encode()
    request = Request(f"{base}{path}?sid={sid}", data=body,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=60) as response:
        response.read()


def upload(base, sid, path):
    """Stream the source through the real multipart /upload route (1 MiB chunks)."""
    boundary = "----cf" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(str(path))[0] or "video/mp4"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def body():
        yield head
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        yield tail

    request = Request(f"{base}/upload?sid={sid}", data=body(),
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                               "Content-Length": str(len(head) + path.stat().st_size + len(tail))})
    with urlopen(request, timeout=3600) as response:
        response.read()


def wait_idle(base, sid, label, log, timeout=7200):
    started = time.time()
    last = None
    while time.time() - started < timeout:
        with urlopen(f"{base}/status?sid={sid}", timeout=30) as response:
            status = json.loads(response.read())
        text = (status["status"], status["message"][:110])
        if text != last:
            log(f"  [{label}] {status['status']} | {status['message'][:110]}")
            last = text
        if not status["busy"]:
            return status, time.time() - started
        time.sleep(2)
    raise TimeoutError(label)


def verify_export(path):
    """Technical verification of an exported MP4: PyAV decodes the whole file."""
    import av
    info = {"path": str(path), "bytes": path.stat().st_size}
    with av.open(str(path)) as container:
        video = next((s for s in container.streams if s.type == "video"), None)
        audio = next((s for s in container.streams if s.type == "audio"), None)
        info["container_duration_s"] = round(float(container.duration / av.time_base), 3) if container.duration else None
        info["video"] = ({"codec": video.codec_context.name, "width": video.codec_context.width,
                          "height": video.codec_context.height,
                          "fps": float(video.average_rate) if video.average_rate else None} if video else None)
        info["audio"] = ({"codec": audio.codec_context.name, "rate": audio.codec_context.sample_rate} if audio else None)
        streams = [s for s in (video, audio) if s is not None]
        counts = {"video": 0, "audio": 0}
        last = {"video": 0.0, "audio": 0.0}
        for packet in container.demux(*streams):
            if packet.dts is None and packet.size == 0:
                continue
            for frame in packet.decode():
                kind = packet.stream.type
                counts[kind] += 1
                if frame.time is not None:
                    last[kind] = float(frame.time)
        info.update({"decoded_video_frames": counts["video"], "decoded_audio_frames": counts["audio"],
                     "last_video_time_s": round(last["video"], 3), "last_audio_time_s": round(last["audio"], 3)})
    return info


def run_one(base, name, source, export_all, log):
    report = {"video": name, "source": str(source), "source_bytes": source.stat().st_size}
    app.ACTIVE_SESSION_ID.set("default")
    sid = app.create_session()
    report["session"] = sid
    started = time.time()
    log(f"== {name}: import ({source.stat().st_size / 1e6:.0f} Mo)")
    upload(base, sid, source)
    report["import_seconds"] = round(time.time() - started, 2)
    app.ACTIVE_SESSION_ID.set(sid)
    state = app.load_studio()
    report["import"] = {"duration": state["duration"], "width": state["width"],
                        "height": state["height"], "status": state["status"]}
    log(f"  import: {state['duration']:.1f}s {state['width']}x{state['height']}")

    log(f"== {name}: analyse narrative complète (Whisper small, VAD puis reprise sans VAD si nécessaire)")
    post_form(base, "/split-parts", sid, {"count": 4})
    _, seconds = wait_idle(base, sid, "analyse", log)
    app.ACTIVE_SESSION_ID.set(sid)
    state = app.load_studio()
    transcript = state.get("transcript") or {}
    report["analysis"] = {
        "wall_seconds": round(seconds, 1), "status": state.get("status"),
        "message": state.get("message"),
        "segments": len(transcript.get("segments", [])), "words": len(transcript.get("words", [])),
        "recovery": transcript.get("transcription_recovery"),
        "performance": transcript.get("performance"),
        "sequences": len((transcript.get("narrative_diagnostics") or {}).get("sequences", [])),
        "parts": [{k: p.get(k) for k in ("index", "start", "end", "duration", "boundary_confidence", "title")}
                  for p in state.get("parts", [])],
    }
    if not state.get("parts"):
        report["error"] = "Aucun épisode produit : " + str(state.get("message"))
        return report

    chosen = state["parts"] if export_all else state["parts"][:1]
    report["exports"] = []
    for part in chosen:
        idx = part.get("index")
        start, end = float(part["start"]), float(part["end"])
        entry = {"part": idx, "start": start, "end": end}
        report["exports"].append(entry)
        log(f"== {name}: épisode {idx} {start:.1f}-{end:.1f}s — transcription de précision et alignement")
        post_form(base, "/select-part", sid, {"start": f"{start:.3f}", "end": f"{end:.3f}"})
        post_form(base, "/transcribe", sid, {"start": f"{start:.3f}", "end": f"{end:.3f}",
                                             "model": "small", "vocabulary": ""})
        _, seconds = wait_idle(base, sid, "transcription", log)
        app.ACTIVE_SESSION_ID.set(sid)
        state = app.load_studio()
        tr = state.get("transcript") or {}
        entry["transcription_seconds"] = round(seconds, 1)
        entry["transcript_status"] = {
            "segments": len(tr.get("segments", [])), "alignment_status": tr.get("alignment_status"),
            "alignment_message": tr.get("alignment_message"), "words": tr.get("total_words"),
            "aligned_words": tr.get("aligned_words"), "studio_status": state.get("status")}
        if not tr.get("segments"):
            entry["error"] = "Transcription vide : " + str(state.get("message"))
            continue
        log(f"== {name}: épisode {idx} — export vertical (cadrage auto)")
        post_form(base, "/make-clip", sid, {"start": f"{start:.3f}", "end": f"{end:.3f}", "hook": "",
                                            "title": f"{name}_ep{idx}", "subtitle_delay": "0",
                                            "framing": "auto", "campaign_id": "validation"})
        _, seconds = wait_idle(base, sid, "export", log)
        app.ACTIVE_SESSION_ID.set(sid)
        state = app.load_studio()
        entry["export_seconds"] = round(seconds, 1)
        entry["export_status"] = state.get("status")
        entry["export_message"] = state.get("message")
        output = state.get("last_output")
        if state.get("status") == "Clip prêt" and output and Path(output).exists():
            entry["verification"] = verify_export(Path(output))
            got = entry["verification"].get("container_duration_s") or 0
            entry["duration_delta_s"] = round(got - (end - start), 3)
        else:
            entry["error"] = "Export non réussi : " + str(state.get("message"))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--export-all", action="store_true")
    parser.add_argument("--only", choices=sorted(SOURCES))
    args = parser.parse_args()
    out_dir = Path(args.out)
    isolate(out_dir)
    log_path = out_dir / "run.log"

    def log(message):
        line = time.strftime("%H:%M:%S ") + message
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    reports = []
    for name, source in SOURCES.items():
        if args.only and name != args.only:
            continue
        try:
            reports.append(run_one(base, name, source, args.export_all, log))
        except Exception as exc:  # keep the other video's result
            log(f"ERREUR {name}: {type(exc).__name__}: {exc}")
            reports.append({"video": name, "error": f"{type(exc).__name__}: {exc}"})
        (out_dir / "report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    server.shutdown()
    log("TERMINE")


if __name__ == "__main__":
    main()
