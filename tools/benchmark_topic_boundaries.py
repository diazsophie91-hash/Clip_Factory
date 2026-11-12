"""Audit QMSum annotation suitability; never treat relevance spans as segmentation gold.

QMSum marks transcript turns relevant to topics and queries. A topic may own
several non-contiguous spans, so these annotations are not a complete,
chronological, mutually-exclusive topic segmentation. This script reports that
structure and deliberately computes no precision, recall, or F1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inspect_document(data: dict) -> dict:
    turns = data.get("meeting_transcripts", []) or []
    topic_list = data.get("topic_list", []) or []
    labels = [set() for _ in turns]
    span_count = 0
    multi_span_topics = 0
    gapped_topics = 0
    invalid_spans = 0
    for topic_i, topic in enumerate(topic_list):
        spans = []
        for raw in topic.get("relevant_text_span", []) or []:
            try:
                start, end = int(raw[0]), int(raw[1])
            except (TypeError, ValueError, IndexError):
                invalid_spans += 1
                continue
            if start < 0 or end < start or start >= len(turns):
                invalid_spans += 1
                continue
            end = min(end, len(turns) - 1)
            spans.append((start, end))
            span_count += 1
            # QMSum uses inclusive transcript-turn endpoints.
            for turn_i in range(start, end + 1):
                labels[turn_i].add(topic_i)
        spans.sort()
        if len(spans) > 1:
            multi_span_topics += 1
            if any(right[0] > left[1] + 1 for left, right in zip(spans, spans[1:])):
                gapped_topics += 1

    unlabeled_turns = sum(not label for label in labels)
    overlapping_turns = sum(len(label) > 1 for label in labels)
    eligible_adjacent_pairs = sum(
        bool(labels[i - 1]) and bool(labels[i]) for i in range(1, len(labels))
    )
    naive_label_set_changes = sum(
        bool(labels[i - 1]) and bool(labels[i]) and labels[i - 1] != labels[i]
        for i in range(1, len(labels))
    )
    return {
        "meeting_id": data.get("meeting_id") or data.get("id"),
        "turns": len(turns),
        "topics": len(topic_list),
        "relevant_spans": span_count,
        "topics_with_multiple_relevant_spans": multi_span_topics,
        "topics_with_noncontiguous_spans": gapped_topics,
        "unlabeled_turns": unlabeled_turns,
        "overlapping_topic_labeled_turns": overlapping_turns,
        "invalid_spans": invalid_spans,
        "old_derived_eligible_adjacent_pairs": eligible_adjacent_pairs,
        "old_derived_label_set_changes_not_valid_gold": naive_label_set_changes,
    }


def audit_split(split_dir: Path) -> dict:
    paths = sorted(split_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No cached QMSum JSON files in {split_dir}")
    rows = []
    hashes = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append(inspect_document(data))
        hashes.append({"file": path.name, "sha256": sha256(path)})
    sum_fields = (
        "turns", "topics", "relevant_spans", "topics_with_multiple_relevant_spans",
        "topics_with_noncontiguous_spans", "unlabeled_turns",
        "overlapping_topic_labeled_turns", "invalid_spans",
        "old_derived_eligible_adjacent_pairs", "old_derived_label_set_changes_not_valid_gold",
    )
    totals = {key: sum(row[key] for row in rows) for key in sum_fields}
    return {"meetings": len(rows), **totals, "meeting_details": rows, "files_sha256": hashes}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ClipFactoryBenchmark" / "qmsum",
        help="Existing local cache; no model or network request is used.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "validation" / "qmsum_topic_benchmark.json",
    )
    args = parser.parse_args()
    val = audit_split(args.cache / "val")
    test = audit_split(args.cache / "test")
    payload = {
        "schema": 2,
        "status": "invalid_for_segmentation",
        "benchmark": "QMSum topic relevance span annotation audit",
        "dataset": "Yale-LILY/QMSum official val/test",
        "span_endpoint_convention": (
            "The previous script treated end indices as inclusive. The official README documents indexed span pairs "
            "but does not state inclusive versus exclusive endpoints; structural counts below reproduce the previous "
            "inclusive assumption and are diagnostic only."
        ),
        "metrics": None,
        "verdict": (
            "QMSum relevant_text_span labels are query/topic relevance spans, not exhaustive, "
            "chronological, mutually-exclusive segmentation labels. No candidate-boundary quality "
            "metrics are validly inferable from the conversion used in the archived exploratory run."
        ),
        "findings": [
            "The previous conversion assigned each topic index to every turn in each inclusive relevant_text_span.",
            "The official README does not specify whether span end indices are inclusive; the old script assumed inclusive endpoints.",
            "It counted a pseudo-boundary when adjacent labeled turns had different topic-index sets.",
            "It excluded every adjacent pair where either turn was unlabeled, so scores were conditioned on annotation coverage.",
            "A topic can have multiple disjoint spans and can recur; its spans do not define all between-topic changes.",
            "Overlapping topic labels make set changes different from a single chronological topic transition.",
            "The old tolerance was +/- 1 transcript turn, not seconds or text similarity.",
            "The old matcher greedily selected the nearest remaining gold point and can undercount maximum one-to-one matches.",
            "The formulas for precision/recall/F1 were standard micro formulas, but their reference events and negative universe were invalid; preserve old values only as exploratory artifacts.",
        ],
        "old_exploratory_result": "validation/history/qmsum_topic_benchmark_20261109_exploratory_invalid.json",
        "val": val,
        "test": test,
        "valid_future_protocol": (
            "Use a corpus with explicit chronological segmentation boundaries and a defined boundary policy, "
            "or evaluate QMSum as its intended query-conditioned relevant-span retrieval task with query-level "
            "span metrics. Do not convert topic relevance sets into segmentation gold."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "metrics": payload["metrics"],
        "val": {k: v for k, v in val.items() if k not in ("meeting_details", "files_sha256")},
        "test": {k: v for k, v in test.items() if k not in ("meeting_details", "files_sha256")},
        "output": str(args.output),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
