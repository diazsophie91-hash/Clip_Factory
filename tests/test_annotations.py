import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from annotation_store import (candidate_id, load_annotations, load_reference_spans,
    load_source_catalog, register_source, save_annotation, save_reference_span, source_fingerprint)
from tools.validation_harness import _load_transcript_input, _metrics


class AnnotationStoreTests(unittest.TestCase):
    def test_validation_harness_loads_transcript_export_and_session_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "sessions").mkdir()
            source = root / "source.mp4"
            source.write_bytes(b"video")
            session_id = "abc123"
            (root / "sessions" / f"session_{session_id}.json").write_text(
                json.dumps({"source": str(source), "duration": 41.0}), encoding="utf-8")
            transcript_path = root / f"transcript_{session_id}.json"
            transcript_path.write_text(json.dumps({"segments": [{"start": 2, "end": 5, "text": "Bonjour."}]}), encoding="utf-8")
            with patch("tools.validation_harness.ROOT", root):
                transcript, canonical_hash, input_hash = _load_transcript_input(transcript_path)
            self.assertEqual(transcript["source"], str(source))
            self.assertEqual(transcript["range_end"], 41.0)
            self.assertEqual(len(canonical_hash), 64)
            self.assertEqual(len(input_hash), 64)

    def test_rating_is_saved_and_updated_without_training(self):
        candidate = {"start": 1.0, "end": 22.0, "text": "Une anecdote qui se termine."}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "annotations.json"
            key = save_annotation("source.mp4", candidate, "good", ["bad_start", "missing_context"], "L'intro manque.", path)
            save_annotation("source.mp4", candidate, "good", ["multiple_scenes_or_topics"], "Deux scènes jointes.", path)
            updated = save_annotation("source.mp4", candidate, "excellent", ["bad_start"], "", path)
            data = load_annotations(path)
            self.assertEqual(key, updated)
            self.assertEqual(data["items"][key]["rating"], "excellent")
            self.assertEqual(data["items"][key]["reasons"], ["bad_start"])
            self.assertEqual(data["items"][key]["comment"], "")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"], 1)

    def test_independent_reference_span_is_saved_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference.json"
            video_hash = "a" * 64
            key = save_reference_span("source.mp4", video_hash, 15, 38, "excellent",
                "Repérée en regardant la source.", "podcast", path)
            same_interval = save_reference_span("source.mp4", video_hash, 15, 38, "good",
                "Justification mise à jour.", "podcast", path)
            self.assertEqual(key, same_interval)
            data = load_reference_spans(path)
            item = data["items"][key]
            self.assertEqual(item["annotation_type"], "independent_reference")
            self.assertEqual(item["provenance"], "manual_source_review")
            self.assertEqual(item["source"], "source.mp4")
            self.assertEqual(item["source_sha256"], video_hash)
            self.assertEqual(item["timeline"], "original_video")
            self.assertEqual(item["narrative_interest"], "good")
            self.assertEqual(item["justification"], "Justification mise à jour.")
            self.assertNotIn("transcript", item)

    def test_source_catalog_hashes_and_classifies_independent_video(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video = root / "clip.mp4"
            video.write_bytes(b"local video bytes")
            catalog = root / "source_catalog.json"
            fingerprint = source_fingerprint(video)
            item = register_source(video, fingerprint, "gaming_commentary", 90, video, catalog)
            self.assertEqual(item["content_type"], "gaming_commentary")
            self.assertEqual(item["source_sha256"], fingerprint)
            self.assertIn(fingerprint, load_source_catalog(catalog)["items"])

    def test_benchmark_recall_uses_independent_references_not_candidate_reviews(self):
        candidate = {"start": 10.0, "end": 40.0, "text": "Un bon passage."}
        gold = {"gold": {"reference_id": "gold", "source": "source.mp4", "source_sha256": "a" * 64,
                         "start": 15.0, "end": 35.0, "narrative_interest": "good"}}
        metrics = _metrics("source.mp4", "a" * 64, [candidate], {}, gold)
        self.assertIsNone(metrics["candidate_review_precision"])
        self.assertEqual(metrics["independent_reference_recall"], 1.0)
        self.assertEqual(metrics["median_start_boundary_error_seconds"], 5.0)

    def test_annotation_rejects_unknown_rating_or_reason(self):
        candidate = {"start": 1.0, "end": 22.0, "text": "Test."}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "annotations.json"
            with self.assertRaises(ValueError):
                save_annotation("source.mp4", candidate, "viral", [], path=path)
            with self.assertRaises(ValueError):
                save_annotation("source.mp4", candidate, "good", ["model_learns"], path=path)
            self.assertNotEqual(candidate_id("source.mp4", candidate), "")
            self.assertFalse(path.exists())

    def test_candidate_card_renders_human_review_controls_and_separate_dimensions(self):
        candidate = {"start": 10.0, "end": 35.0, "duration": 25.0, "score": 72.0,
            "text": "Une histoire test.", "title": "Essai", "reasons": [], "words": 5,
            "confidence": 0.9, "speech_coverage": 0.8, "scores": {"narrative_interest": 55,
            "comprehensibility": 80, "transcript_quality": 90, "autonomy": 70,
            "audio_quality": None, "visual_quality": None, "campaign_fit": 100}, "defects": [],
            "score_method": "indice de tri heuristique explicable, sans mesure de viralité"}
        studio = {"source": "C:/video.mp4", "duration": 60, "width": 1920, "height": 1080,
            "suggestions": [candidate], "transcript": {"segments": []}, "scan_full": True,
            "operation": "candidates", "analysis_mode": "highlights"}
        with patch("app.load_studio", return_value=studio), \
             patch("app.load_data", return_value={"campaigns": [], "clips": []}), \
             patch("app.load_annotations", return_value={"schema": 1, "items": {}}), \
             patch("app.load_reference_spans", return_value={"schema": 1, "items": {}}), \
             patch("app.load_source_catalog", return_value={"schema": 1, "items": {}}), \
             patch("app.session_worker_active", return_value=False):
            rendered = app.studio_page()
        for label in ("Évaluer cette proposition (facultatif)", "Évaluation humaine", "Mauvais début", "Mauvaise transcription",
                      "Plusieurs scènes ou sujets", "Intérêt narratif estimé", "sans mesure de viralité", "/annotate-candidate"):
            self.assertIn(label, rendered)
        self.assertIn("Outil facultatif de validation", rendered)
        self.assertIn("n'est pas nécessaire pour générer", rendered)
        self.assertIn("/reference-review?sid=", rendered)

    def test_blind_reference_view_has_no_engine_candidates_or_transcript(self):
        studio = {"source": "C:/video.mp4", "duration": 60, "width": 1920, "height": 1080,
            "suggestions": [{"text": "Proposition qui ne doit pas apparaître."}],
            "transcript": {"segments": [{"text": "Texte Whisper qui ne doit pas apparaître."}]}}
        with patch("app.load_studio", return_value=studio), \
             patch("app.load_reference_spans", return_value={"schema": 1, "items": {}}), \
             patch("app.load_source_catalog", return_value={"schema": 1, "items": {}}):
            rendered = app.reference_review_page()
        self.assertIn("/annotate-missed?sid=", rendered)
        self.assertIn("Type principal de contenu", rendered)
        self.assertIn("Intérêt narratif", rendered)
        self.assertIn("Justification facultative", rendered)
        self.assertIn("timeline originale", rendered)
        self.assertNotIn("Proposition qui ne doit pas apparaître", rendered)
        self.assertNotIn("Texte Whisper qui ne doit pas apparaître", rendered)


if __name__ == "__main__":
    unittest.main()
