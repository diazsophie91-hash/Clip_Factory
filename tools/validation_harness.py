"""Reproducible comparison of the frozen reference and candidate ranker profiles.

All evaluation is local and deterministic. Human labels are read from two separate
stores: candidate_reviews (precision) and reference_spans (independent recall).
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from statistics import median

from annotation_store import candidate_id, load_annotations, load_reference_spans, load_source_catalog

ROOT = Path(__file__).resolve().parents[1]
TRANSCRIPTS = ROOT / "validation" / "transcripts"
BASELINE_ENGINE = ROOT / "validation" / "baseline_content_engine.py"
BASELINE_RESULTS = ROOT / "validation" / "baseline_results.json"
BASELINE_MANIFEST = ROOT / "validation" / "baseline_manifest.json"
DEFAULT_OUTPUT = ROOT / "validation" / "latest_comparison.json"

PROFILES = {
    "current": {},
    "narrative_weighted": {"weights": {"narrative_interest": 0.45, "comprehensibility": 0.20,
        "transcript_quality": 0.20, "autonomy": 0.15}},
    "transcription_weighted": {"weights": {"narrative_interest": 0.20, "comprehensibility": 0.15,
        "transcript_quality": 0.45, "autonomy": 0.20}},
    "sentence_pause_no_grid": {"anchor_mode": "sentence_pause", "use_grid": False},
    "sentence_only_no_grid": {"anchor_mode": "sentence_only", "use_grid": False},
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _source_file(source):
    """Resolve the original media path saved by a validation transcript/session."""
    candidate = Path(str(source or ""))
    if candidate.is_file():
        return candidate
    candidate = ROOT / "media" / "sources" / candidate.name
    return candidate if candidate.is_file() else None


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load engine snapshot: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_baseline_cache():
    try:
        data = json.loads(BASELINE_RESULTS.read_text(encoding="utf-8"))
        return {Path(item["transcript"]).name: item for item in data.get("runs", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _load_transcript_input(path):
    """Read a validation transcript or a saved session transcript, excluding session results."""
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    transcript = payload.get("transcript") if isinstance(payload, dict) and isinstance(payload.get("transcript"), dict) else payload
    transcript = copy.deepcopy(transcript)
    parent = payload if transcript is not payload else {}
    source = transcript.get("source") or parent.get("source")
    if not source:
        match = re.fullmatch(r"transcript_(.+)\.json", path.name)
        if match:
            session_file = ROOT / "sessions" / f"session_{match.group(1)}.json"
            if session_file.is_file():
                session = json.loads(session_file.read_text(encoding="utf-8"))
                parent = session
                source = session.get("source")
                if isinstance(session.get("transcript"), dict):
                    transcript = copy.deepcopy(session["transcript"])
        elif re.fullmatch(r"session_.+\.json", path.name):
            source = parent.get("source")
    if not source:
        raise ValueError(f"No source video path is recorded for transcript input {path}.")
    transcript["source"] = source
    if "range_end" not in transcript and parent.get("duration") is not None:
        transcript["range_end"] = parent["duration"]
    canonical = json.dumps(transcript, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    transcript_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return transcript, transcript_hash, sha256_file(path)


def write_baseline_manifest(transcript_paths=None, min_duration=20, max_duration=45, limit=8, output=BASELINE_MANIFEST):
    """Freeze hashes that bind the original diagnostics to their exact inputs."""
    transcript_paths = list(transcript_paths or sorted(TRANSCRIPTS.glob("*_small.json")))
    baseline = _load_module("manifest_content_engine", BASELINE_ENGINE)
    cached = _load_baseline_cache()
    runs = []
    for path in transcript_paths:
        path = Path(path)
        transcript, transcript_hash, input_file_hash = _load_transcript_input(path)
        diagnostics = {}
        selected = baseline.rank_candidate_excerpts(transcript, min_duration, max_duration, limit, diagnostics=diagnostics)
        old = cached.get(path.name)
        if (min_duration, max_duration, limit) == (20, 45, 8) and old and \
                old.get("input_file_sha256", old.get("transcript_sha256")) == input_file_hash and old.get("baseline_candidates") != selected:
            raise RuntimeError(f"Frozen baseline differs from the saved baseline result for {path.name}.")
        source = transcript.get("source")
        source_path = _source_file(source)
        runs.append({"transcript": path.name, "transcript_sha256": transcript_hash,
                     "input_file_sha256": input_file_hash,
                     "min_duration": min_duration, "max_duration": max_duration, "limit": limit,
                     "source_video_sha256": sha256_file(source_path) if source_path else None,
                     "baseline_selected": selected, "diagnostics": diagnostics})
    manifest = {"schema": 1, "baseline_engine": str(BASELINE_ENGINE.relative_to(ROOT)),
                "baseline_engine_sha256": sha256_file(BASELINE_ENGINE),
                "min_duration": min_duration, "max_duration": max_duration, "limit": limit,
                "runs": runs}
    for row in runs:
        row["engine_sha256"] = manifest["baseline_engine_sha256"]
    Path(output).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _manifest_map(min_duration=20, max_duration=45, limit=8):
    try:
        manifest = json.loads(BASELINE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = write_baseline_manifest(min_duration=min_duration, max_duration=max_duration, limit=limit)
    if manifest.get("baseline_engine_sha256") != sha256_file(BASELINE_ENGINE):
        raise RuntimeError("Frozen baseline engine hash changed; update the validation manifest deliberately.")
    return {(row["transcript"], row["transcript_sha256"], row.get("min_duration"),
             row.get("max_duration"), row.get("limit")): row for row in manifest.get("runs", [])}


def _metrics(source_name, source_sha256, candidates, candidate_reviews, gold_spans):
    source_reviews = []
    for candidate in candidates:
        key = candidate_id(source_name, candidate)
        review = candidate_reviews.get(key)
        if review and (not review.get("source_sha256") or review.get("source_sha256") == source_sha256):
            source_reviews.append((candidate, review))
    ratings = [review.get("rating") for _, review in source_reviews]
    rated_count = len(ratings)
    positive = sum(rating in ("excellent", "good") for rating in ratings)
    negative = sum(rating in ("bad", "unusable") for rating in ratings)
    correction = sum(bool(set(review.get("reasons", [])) & {"bad_start", "bad_end"})
                     for _, review in source_reviews)
    reason_counts = {}
    for _, review in source_reviews:
        for reason in review.get("reasons", []):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    source_gold = [item for item in gold_spans.values()
                   if item.get("source_sha256") == source_sha256 and
                   item.get("narrative_interest") in ("excellent", "good")]
    matched = []
    missed = []
    for gold in source_gold:
        ga, gb = float(gold["start"]), float(gold["end"])
        gd = max(0.001, gb - ga)
        options = []
        for candidate in candidates:
            ca, cb = float(candidate["start"]), float(candidate["end"])
            overlap = max(0.0, min(gb, cb) - max(ga, ca))
            recall_overlap = overlap / gd
            candidate_coverage = overlap / max(0.001, cb - ca)
            options.append((recall_overlap, candidate_coverage, candidate))
        best = max(options, key=lambda row: (row[0], row[1]), default=(0.0, 0.0, None))
        if best[0] >= 0.5 and best[1] >= 0.2:
            c = best[2]
            matched.append({"reference_id": gold.get("reference_id"),
                "coverage": round(best[0], 4), "candidate_coverage": round(best[1], 4),
                "start_error_seconds": round(abs(float(c["start"]) - ga), 3),
                "end_error_seconds": round(abs(float(c["end"]) - gb), 3)})
        else:
            missed.append(gold.get("reference_id"))
    return {
        "candidate_review_count": rated_count,
        "candidate_review_precision": round(positive / rated_count, 4) if rated_count else None,
        "candidate_review_bad_rate": round(negative / rated_count, 4) if rated_count else None,
        "candidate_review_boundary_correction_rate": round(correction / rated_count, 4) if rated_count else None,
        "candidate_review_reason_counts": reason_counts,
        "independent_positive_reference_count": len(source_gold),
        "independent_reference_recall": round(len(matched) / len(source_gold), 4) if source_gold else None,
        "reference_matches": matched,
        "missed_reference_ids": missed,
        "median_start_boundary_error_seconds": round(median(x["start_error_seconds"] for x in matched), 3) if matched else None,
        "median_end_boundary_error_seconds": round(median(x["end_error_seconds"] for x in matched), 3) if matched else None,
    }


def run_comparison(transcript_paths=None, output=DEFAULT_OUTPUT, min_duration=20, max_duration=45, limit=8):
    """Run all engine/profile versions on the same stored transcript inputs."""
    transcript_paths = list(transcript_paths or sorted(TRANSCRIPTS.glob("*_small.json")))
    if not transcript_paths:
        raise ValueError(f"No transcript JSON files found under {TRANSCRIPTS}")
    manifest = _manifest_map(min_duration, max_duration, limit)
    baseline = _load_module("frozen_content_engine", BASELINE_ENGINE)
    import content_engine as current_engine
    candidate_reviews = load_annotations().get("items", {})
    reference_data = load_reference_spans().get("items", {})
    source_catalog = load_source_catalog().get("items", {})
    results = []
    source_hashes = {}
    for transcript_path in transcript_paths:
        path = Path(transcript_path)
        transcript, transcript_hash, input_file_hash = _load_transcript_input(path)
        source_name = Path(str(transcript.get("source") or path.stem)).name
        source_path = _source_file(transcript.get("source"))
        if source_name not in source_hashes:
            source_hashes[source_name] = sha256_file(source_path) if source_path else None
        versions = []
        baseline_row = manifest.get((path.name, transcript_hash, min_duration, max_duration, limit))
        baseline_diagnostics = {}
        baseline_candidates = baseline.rank_candidate_excerpts(transcript, min_duration, max_duration,
            limit, diagnostics=baseline_diagnostics)
        baseline_verified = False
        if baseline_row:
            cached = baseline_row.get("baseline_selected", [])
            if baseline_candidates != cached:
                raise RuntimeError(f"Frozen baseline output changed for {path.name}; refusing a non-reproducible run.")
            baseline_verified = True
        base_candidates = copy.deepcopy(baseline_candidates)
        for item in base_candidates:
            item.setdefault("candidate_id", candidate_id(source_name, item))
        versions.append({"version": "reference", "engine_sha256": sha256_file(BASELINE_ENGINE),
            "baseline_output_verified_against_manifest": baseline_verified,
            "raw_candidate_count": baseline_diagnostics.get("raw_candidates"),
            "kept_candidate_count": len(base_candidates),
            "diagnostics": baseline_diagnostics,
            "candidates": base_candidates,
            "metrics": _metrics(source_name, source_hashes[source_name], base_candidates, candidate_reviews, reference_data)})

        for profile_name, config in PROFILES.items():
            diagnostics = {}
            candidates = current_engine.rank_candidate_excerpts(transcript, min_duration, max_duration,
                limit, diagnostics=diagnostics, config=config)
            versions.append({"version": profile_name, "engine_sha256": sha256_file(Path(current_engine.__file__)),
                "config": config, "raw_candidate_count": diagnostics.get("raw_candidates"),
                "kept_candidate_count": len(candidates), "diagnostics": diagnostics,
                "candidates": candidates,
                "metrics": _metrics(source_name, source_hashes[source_name], candidates, candidate_reviews, reference_data)})
        source_metadata = source_catalog.get(source_hashes[source_name] or "", {})
        results.append({"transcript": path.name, "transcript_sha256": transcript_hash,
            "input_file_sha256": input_file_hash,
            "transcription_model": transcript.get("model"), "language": transcript.get("language"),
            "source_video_sha256": source_hashes[source_name],
            "content_type": source_metadata.get("content_type"),
            "duration_seconds": transcript.get("range_end"),
            "source": source_name, "segments": len(transcript.get("segments", [])),
            "normalized_words": len(current_engine._normal_words(transcript)), "versions": versions})
    payload = {"schema": 1, "evaluation": "human annotations only; no ratings are inferred",
        "min_duration": min_duration, "max_duration": max_duration, "result_limit": limit,
        "baseline_engine_sha256": sha256_file(BASELINE_ENGINE),
        "candidate_engine_sha256": sha256_file(Path(current_engine.__file__)),
        "benchmark_harness_sha256": sha256_file(Path(__file__)),
        "candidate_annotations_sha256": sha256_file(ROOT / "validation" / "annotations.json"),
        "independent_references_sha256": sha256_file(ROOT / "validation" / "reference_spans.json"),
        "source_catalog_sha256": sha256_file(ROOT / "validation" / "source_catalog.json"),
        "runs": results}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
