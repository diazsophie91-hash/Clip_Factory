import json
import unittest
from pathlib import Path

from tools.benchmark_topic_boundaries import inspect_document


class QMSumAnnotationAuditTests(unittest.TestCase):
    def test_noncontiguous_relevance_spans_are_reported_not_bridged_as_gold(self):
        document = {
            "meeting_transcripts": [{"content": str(i)} for i in range(5)],
            "topic_list": [{"topic": "A", "relevant_text_span": [[0, 0], [4, 4]]}],
        }
        audit = inspect_document(document)
        self.assertEqual(audit["topics_with_multiple_relevant_spans"], 1)
        self.assertEqual(audit["topics_with_noncontiguous_spans"], 1)
        self.assertEqual(audit["unlabeled_turns"], 3)
        self.assertEqual(audit["old_derived_eligible_adjacent_pairs"], 0)
        self.assertEqual(audit["old_derived_label_set_changes_not_valid_gold"], 0)

    def test_overlapping_relevance_labels_create_set_changes_without_exclusive_topic_boundaries(self):
        document = {
            "meeting_transcripts": [{"content": str(i)} for i in range(3)],
            "topic_list": [
                {"topic": "A", "relevant_text_span": [[0, 2]]},
                {"topic": "B", "relevant_text_span": [[1, 1]]},
            ],
        }
        audit = inspect_document(document)
        self.assertEqual(audit["overlapping_topic_labeled_turns"], 1)
        self.assertEqual(audit["old_derived_eligible_adjacent_pairs"], 2)
        self.assertEqual(audit["old_derived_label_set_changes_not_valid_gold"], 2)

    def test_published_qmsum_metric_file_is_invalidated_but_history_is_preserved(self):
        root = Path(__file__).resolve().parents[1] / "validation"
        current = json.loads((root / "qmsum_topic_benchmark.json").read_text(encoding="utf-8"))
        history = json.loads((root / "history" / "qmsum_topic_benchmark_20261109_exploratory_invalid.json").read_text(encoding="utf-8"))
        self.assertEqual(current["status"], "invalid_for_segmentation")
        self.assertIsNone(current["metrics"])
        self.assertEqual(history["benchmark"], "QMSum turn-level topic-span transitions")
        self.assertEqual(history["results"][1]["test"]["tp"], 10)


if __name__ == "__main__":
    unittest.main()
