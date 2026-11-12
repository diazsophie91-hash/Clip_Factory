"""Reproduce episode-boundary comparisons on the two saved local transcripts."""
import hashlib
import json
import statistics
import time
from pathlib import Path

from content_engine import build_story_parts, detect_narrative_sequences

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "validation" / "episode_segmentation_baseline_20261111.json"
OUTPUT = ROOT / "validation" / "episode_segmentation_after_20261111.json"
SOURCE_IDS = ("source_5efb9fef42", "source_57e0fbd52b")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    old = json.loads(BASELINE.read_text(encoding="utf-8"))
    result = {
        "captured_after": "2026-11-11",
        "method": "same saved transcripts, count=4; five runs for median CPU time",
        "engine_sha256": sha256(ROOT / "content_engine.py"),
        "runs": {},
    }
    for source_id in SOURCE_IDS:
        transcript_path = ROOT / "validation" / "transcripts" / f"{source_id}_small.json"
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        sequence_times, part_times = [], []
        sequences = parts = None
        for _ in range(5):
            started = time.perf_counter()
            sequences = detect_narrative_sequences(transcript)
            sequence_times.append(time.perf_counter() - started)
            started = time.perf_counter()
            parts = build_story_parts(transcript, 4, transcript["range_end"])
            part_times.append(time.perf_counter() - started)
        previous_parts = old[source_id]["parts"]
        previous_first_end = previous_parts[0]["end"]
        new_first_end = parts[0]["end"]
        video_path = ROOT / "media" / "sources" / f"{source_id}.mp4"
        result["runs"][source_id] = {
            "transcript": transcript_path.name,
            "transcript_sha256": sha256(transcript_path),
            "video_sha256": sha256(video_path) if video_path.exists() else None,
            "duration_seconds": transcript["range_end"],
            "baseline_parts": previous_parts,
            "baseline_first_part_end": previous_first_end,
            "new_first_part_end": new_first_end,
            "first_part_end_change_seconds": round(new_first_end - previous_first_end, 3),
            "sequence_count": len(sequences),
            "sequences": [{"start": s["start"], "end": s["end"], "title": s["title"],
                           "start_evidence": s["start_evidence"],
                           "end_evidence": s["end_evidence"]} for s in sequences],
            "new_parts": parts,
            "sequence_detection_median_seconds": round(statistics.median(sequence_times), 6),
            "episode_build_median_seconds": round(statistics.median(part_times), 6),
        }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "engine_sha256": result["engine_sha256"],
                      "runs": {key: {"baseline_first_part_end": value["baseline_first_part_end"],
                                     "new_first_part_end": value["new_first_part_end"],
                                     "sequences": value["sequence_count"],
                                     "parts": len(value["new_parts"]),
                                     "sequence_median_seconds": value["sequence_detection_median_seconds"],
                                     "parts_median_seconds": value["episode_build_median_seconds"]}
                               for key, value in result["runs"].items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
