"""Human review labels for excerpt validation. This store does not train a model."""
import hashlib
import json
import threading
from pathlib import Path

ANNOTATION_PATH = Path(__file__).resolve().parent / "validation" / "annotations.json"
REFERENCE_PATH = Path(__file__).resolve().parent / "validation" / "reference_spans.json"
SOURCE_CATALOG_PATH = Path(__file__).resolve().parent / "validation" / "source_catalog.json"
RATINGS = ("excellent", "good", "medium", "bad", "unusable")
SOURCE_TYPES = ("podcast", "interview", "entertainment", "humor", "gaming_commentary", "conversation", "narrative", "other")
REASONS = (
    "bad_start", "bad_end", "missing_context", "not_interesting", "repetitive",
    "bad_transcription", "too_much_silence", "bad_audiovisual_interest",
    "multiple_scenes_or_topics",
)
_LOCK = threading.RLock()
_HASH_CACHE = {}


def candidate_id(source, candidate):
    """Return a stable id for one cut, tied to its source identity and transcript."""
    payload = "\0".join((Path(str(source or "unknown")).name,
                         f"{float(candidate['start']):.3f}",
                         f"{float(candidate['end']):.3f}",
                         str(candidate.get("text", ""))))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def load_annotations(path=ANNOTATION_PATH):
    path = Path(path)
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("schema") == 1 and isinstance(data.get("items"), dict) else {"schema": 1, "items": {}}
        except (OSError, ValueError, TypeError):
            return {"schema": 1, "items": {}}


def load_reference_spans(path=REFERENCE_PATH):
    path = Path(path)
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("schema") == 1 and isinstance(data.get("items"), dict) else {"schema": 1, "items": {}}
        except (OSError, ValueError, TypeError):
            return {"schema": 1, "items": {}}


def load_source_catalog(path=SOURCE_CATALOG_PATH):
    path = Path(path)
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("schema") == 1 and isinstance(data.get("items"), dict) else {"schema": 1, "items": {}}
        except (OSError, ValueError, TypeError):
            return {"schema": 1, "items": {}}


def source_fingerprint(path):
    """Hash a local source once and cache it by size/mtime to avoid repeat reads."""
    path = Path(path)
    stat = path.stat()
    cache_key = str(path.resolve())
    cached = _HASH_CACHE.get(cache_key)
    signature = (stat.st_size, stat.st_mtime_ns)
    if cached and cached[0] == signature:
        return cached[1]
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    value = digest.hexdigest()
    _HASH_CACHE[cache_key] = (signature, value)
    return value


def register_source(source, source_sha256, content_type, duration=None, video_path=None, path=SOURCE_CATALOG_PATH):
    source_type = str(content_type or "").strip()
    if source_type not in SOURCE_TYPES:
        raise ValueError("Choisis le type principal du contenu pour cette vidéo.")
    if not re_full_sha256(source_sha256):
        raise ValueError("L'empreinte SHA-256 de la source est invalide.")
    source_name = Path(str(source or "unknown")).name
    metadata = {"source_sha256": source_sha256, "source": source_name, "content_type": source_type}
    if duration is not None:
        metadata["duration_seconds"] = round(float(duration), 3)
    if video_path and Path(video_path).is_file():
        stat = Path(video_path).stat()
        metadata["file_size"] = stat.st_size
        metadata["file_mtime_ns"] = stat.st_mtime_ns
    path = Path(path)
    with _LOCK:
        data = load_source_catalog(path)
        data["items"][source_sha256] = metadata
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return metadata


def re_full_sha256(value):
    value = str(value or "")
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def save_annotation(source, candidate, rating, reasons=(), comment="", path=ANNOTATION_PATH, source_sha256=None):
    rating = str(rating or "").strip().lower()
    if rating not in RATINGS:
        raise ValueError("Choisis une évaluation parmi excellent, bon, moyen, mauvais ou inutilisable.")
    normalized_reasons = sorted({str(x).strip() for x in (reasons or ()) if str(x).strip()})
    if any(x not in REASONS for x in normalized_reasons):
        raise ValueError("Une raison d'annotation n'est pas reconnue.")
    key = candidate_id(source, candidate)
    if source_sha256 is not None and not re_full_sha256(source_sha256):
        raise ValueError("L'empreinte SHA-256 de la source est invalide.")
    path = Path(path)
    with _LOCK:
        data = load_annotations(path)
        data["items"][key] = {
            "annotation_type": "generated_candidate_review",
            "source": Path(str(source or "unknown")).name,
            "source_sha256": source_sha256,
            "start": round(float(candidate["start"]), 3),
            "end": round(float(candidate["end"]), 3),
            "text": str(candidate.get("text", "")),
            "rating": rating,
            "reasons": normalized_reasons,
            "comment": str(comment or "").strip()[:1000],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return key


def save_reference_span(source, source_sha256, start, end, narrative_interest, justification="", content_type="", path=REFERENCE_PATH):
    """Save a manually selected source interval, separate from transcript/model output."""
    start, end = float(start), float(end)
    if start < 0 or end <= start:
        raise ValueError("Les temps du passage de référence sont invalides.")
    narrative_interest = str(narrative_interest or "").strip().lower()
    if narrative_interest not in RATINGS:
        raise ValueError("Choisis une évaluation parmi excellent, bon, moyen, mauvais ou inutilisable.")
    if not re_full_sha256(source_sha256):
        raise ValueError("L'empreinte SHA-256 de la source est invalide.")
    source_name = Path(str(source or "unknown")).name
    content_type = str(content_type or "").strip()
    if content_type and content_type not in SOURCE_TYPES:
        raise ValueError("Le type principal de contenu n'est pas reconnu.")
    # Identity depends on original source hash and timeline only, never transcript
    # text or an engine candidate id.
    payload = f"{source_sha256}\0{start:.3f}\0{end:.3f}"
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    path = Path(path)
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema") != 1 or not isinstance(data.get("items"), dict):
                raise ValueError("schema")
        except (OSError, ValueError, TypeError):
            data = {"schema": 1, "items": {}}
        data["items"][key] = {
            "annotation_type": "independent_reference", "reference_id": key,
            "source_sha256": source_sha256, "source": source_name,
            "start": round(start, 3), "end": round(end, 3),
            "timeline": "original_video", "narrative_interest": narrative_interest,
            "justification": str(justification or "").strip()[:1000],
            "content_type": content_type, "provenance": "manual_source_review",
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return key
