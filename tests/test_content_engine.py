import math
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
from content_engine import detect_narrative_sequences


def make_transcript(sentence_count=36, include_probabilities=True):
    sentences = []
    cursor = 0.0
    words = []
    for i in range(sentence_count):
        phrases = [
            ["Au", "début", "personne", "ne", "connaissait", "la", "réponse"],
            ["Mais", "ensuite", "une", "preuve", "change", "complètement", "la", "situation"],
            ["Pourquoi", "ce", "résultat", "surprend", "toute", "l'équipe", "aujourd'hui"],
            ["Finalement", "le", "choix", "le", "plus", "simple", "devient", "le", "meilleur"],
        ]
        tokens = phrases[i % len(phrases)].copy()
        tokens += [f"détail{i}", "reste", "important"]
        tokens[-1] += "." if i % 2 else "?"
        seg_words = []
        for j, token in enumerate(tokens):
            start = cursor + j * 0.52
            item = {"word": token, "start": start, "end": start + 0.38}
            if include_probabilities:
                item["probability"] = 0.86
            seg_words.append(item)
            words.append(item.copy())
        end = seg_words[-1]["end"]
        sentences.append({"start": cursor, "end": end, "text": " ".join(tokens), "words": seg_words,
                          "avg_logprob": -0.2, "no_speech_prob": 0.01, "compression_ratio": 1.2})
        cursor = end + 0.42
    return {"segments": sentences, "words": words, "range_start": 0.0, "range_end": cursor}


class CandidateEngineTests(unittest.TestCase):
    def test_campaign_duration_and_result_limit_are_applied(self):
        transcript = make_transcript()
        result = app.rank_candidate_excerpts(transcript, 12, 18, 5)
        self.assertGreater(len(result), 0)
        self.assertLessEqual(len(result), 5)
        for candidate in result:
            self.assertGreaterEqual(candidate["duration"], 12)
            self.assertLessEqual(candidate["duration"], 18)

    def test_scores_are_explainable_and_audiovisual_is_not_fabricated(self):
        result = app.rank_candidate_excerpts(make_transcript(), 20, 45, 4)
        self.assertTrue(result)
        for candidate in result:
            self.assertIn("sans mesure de viralité", candidate["score_method"])
            self.assertIsNone(candidate["scores"]["audiovisual"])
            self.assertTrue(0 <= candidate["score"] <= 100)
            self.assertTrue(math.isfinite(candidate["scores"]["transcript_quality"]))
            self.assertIn("autonomy", candidate["scores"])
            self.assertIn("narrative_interest", candidate["scores"])
            self.assertIn("comprehensibility", candidate["scores"])
            self.assertIsNone(candidate["scores"]["audio_quality"])
            self.assertIsNone(candidate["scores"]["visual_quality"])
            self.assertIn("sans mesure de viralité", candidate["score_method"])

    def test_unknown_word_confidence_is_not_treated_as_certain(self):
        transcript = make_transcript(include_probabilities=False)
        words = app._normal_words(transcript)
        self.assertAlmostEqual(words[0]["probability"], 0.55)

    def test_known_subtitle_credit_hallucination_is_rejected(self):
        text = "Sous Sous Sous Sous la Sous-titres réalisés par la communauté Amara"
        transcript = {"segments": [{"start": 0, "end": 30, "text": text}], "range_start": 0, "range_end": 30}
        self.assertEqual(app.rank_candidate_excerpts(transcript, 20, 45, 8), [])

    def test_speech_sparse_windows_are_rejected_instead_of_scored_as_clips(self):
        tokens = ["Une", "phrase", "calme", "mais", "complète."]
        timed = []
        for base in (0.0, 30.0):
            for i, token in enumerate(tokens):
                start = base + i * 0.2
                timed.append({"word": token, "start": start, "end": start + 0.12, "probability": 0.9})
        transcript = {"range_start": 0, "range_end": 60, "segments": [{"start": 0, "end": 60,
            "text": "Une phrase calme mais complète.", "words": timed}]}
        stats = {}
        result = app.rank_candidate_excerpts(transcript, 20, 45, 8, diagnostics=stats)
        self.assertEqual(result, [])
        self.assertGreater(stats["rejected_sparse_windows"], 0)
        self.assertTrue(any(row.get("reason") == "speech_coverage_below_threshold"
                            for row in stats["candidate_audit"]))

    def test_candidate_profiles_record_weighting_and_segmentation_provenance(self):
        transcript = make_transcript()
        stats = {}
        result = app.rank_candidate_excerpts(transcript, 20, 45, 4,
            diagnostics=stats,
            config={"anchor_mode": "sentence_pause", "use_grid": False,
                    "weights": {"narrative_interest": 0.5, "comprehensibility": 0.1,
                                "transcript_quality": 0.2, "autonomy": 0.2}})
        self.assertTrue(result)
        self.assertEqual(stats["configuration"]["anchor_mode"], "sentence_pause")
        self.assertFalse(stats["configuration"]["use_grid"])
        self.assertAlmostEqual(sum(stats["configuration"]["weights"].values()), 1.0)
        self.assertTrue(all(item.get("candidate_id") for item in result))
        self.assertTrue(any(row.get("decision") == "selected" for row in stats["candidate_audit"]))

    def test_local_validation_transcriptions_run_and_report_raw_candidate_counts(self):
        root = app.ROOT / "validation" / "transcripts"
        if not root.exists():
            self.skipTest("No local validation transcriptions")
        files = list(root.glob("*_small.json"))
        self.assertGreaterEqual(len(files), 2)
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            stats = {}
            app.rank_candidate_excerpts(data, 20, 45, 8, diagnostics=stats)
            self.assertGreater(stats["input_segments"], 0)
            self.assertGreater(stats["candidate_windows_considered"], 0)
            self.assertGreaterEqual(stats["selected_candidates"], 0)

    def test_story_parts_cover_the_source_without_gaps_or_overlaps(self):
        transcript = make_transcript(28)
        parts = app.build_story_parts(transcript, 4, transcript["range_end"])
        self.assertGreaterEqual(len(parts), 2)
        self.assertAlmostEqual(parts[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(parts[-1]["end"], transcript["range_end"], places=2)
        for previous, current in zip(parts, parts[1:]):
            self.assertAlmostEqual(previous["end"], current["start"], places=2)
            self.assertLessEqual(previous["end"], current["start"] + 0.01)

    def test_one_worker_per_session_is_enforced(self):
        started = threading.Event()
        release = threading.Event()
        previous = app.ACTIVE_SESSION_ID.get()
        app.ACTIVE_SESSION_ID.set("worker-regression-test")
        try:
            def wait_worker():
                started.set()
                release.wait(timeout=2)

            thread = app.spawn_session_worker(wait_worker)
            self.assertTrue(started.wait(timeout=1))
            with self.assertRaisesRegex(RuntimeError, "déjà actif"):
                app.spawn_session_worker(lambda: None)
            release.set()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        finally:
            release.set()
            app.ACTIVE_SESSION_ID.set(previous)


class SparseVADRecoveryTests(unittest.TestCase):
    def test_episode_mode_reuses_accepted_no_vad_transcript(self):
        with tempfile.TemporaryDirectory(prefix="clip-factory-vad-test-") as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"test source")
            media = root / "media"
            sessions = root / "sessions"
            sid = "episode-vad-test"
            transcript_used_for_parts = []

            class FakeModel:
                def __init__(self, *args, **kwargs):
                    self.vad_modes = []

                def transcribe(self, *args, **kwargs):
                    self.vad_modes.append(kwargs["vad_filter"])
                    marker = " retry wording" if not kwargs["vad_filter"] else " sparse wording"
                    word = SimpleNamespace(start=1.0, end=1.4, word=marker, probability=0.95)
                    segment = SimpleNamespace(start=1.0, end=2.0, text=marker,
                        words=[word], avg_logprob=-0.2, no_speech_prob=0.01,
                        compression_ratio=1.1)
                    return iter([segment]), SimpleNamespace(language="fr")

            fake_model = FakeModel()
            fake_module = SimpleNamespace(WhisperModel=lambda *args, **kwargs: fake_model)

            def rank(transcript, *args, diagnostics=None, **kwargs):
                if "retry wording" in transcript["segments"][0]["text"]:
                    diagnostics.update({"normalized_words": 100,
                                        "candidate_windows_considered": 1,
                                        "rejected_sparse_windows": 0})
                    return [{"start": 0, "end": 30}]
                diagnostics.update({"normalized_words": 42,
                                    "candidate_windows_considered": 2,
                                    "rejected_sparse_windows": 2})
                return []

            def build_parts(transcript, count, duration):
                transcript_used_for_parts.append(transcript)
                return [{"index": 1, "start": 0.0, "end": 30.0,
                         "boundary_confidence": "corroborated"},
                        {"index": 2, "start": 30.0, "end": 60.0,
                         "boundary_confidence": "corroborated"}]

            previous_sid = app.ACTIVE_SESSION_ID.get()
            app.ACTIVE_SESSION_ID.set(sid)
            try:
                with patch.object(app, "MEDIA", media), \
                     patch.object(app, "SESSIONS_DIR", sessions), \
                     patch.object(app, "ffmpeg_exe", return_value="ffmpeg"), \
                     patch.object(app.subprocess, "run", side_effect=lambda cmd, **kw: (
                         Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True),
                         Path(cmd[-1]).write_bytes(b"x" * 2048),
                         SimpleNamespace(returncode=0, stderr=""))[2]), \
                     patch.dict(sys.modules, {"faster_whisper": fake_module}), \
                     patch.object(app, "rank_candidate_excerpts", side_effect=rank), \
                     patch.object(app, "build_story_parts", side_effect=build_parts), \
                     patch.object(app, "detect_narrative_sequences", return_value=[]):
                    app.save_studio({"source": str(source), "duration": 60,
                                     "status": "ready", "transcript": None})
                    app.transcribe_worker("small", 0, 60, False, False, True, 2)
                    saved = app.load_studio()
                self.assertEqual(fake_model.vad_modes, [True, False])
                self.assertTrue(saved["transcript"]["transcription_recovery"]["used"])
                self.assertIn("retry wording", saved["transcript"]["segments"][0]["text"])
                self.assertIn("retry wording", transcript_used_for_parts[0]["segments"][0]["text"])
                self.assertIn("narrative_diagnostics", saved["transcript"])
            finally:
                app.ACTIVE_SESSION_ID.set(previous_sid)

    def test_episode_mode_does_not_retry_without_sparse_window_evidence(self):
        self.assertFalse(app._should_retry_sparse_vad_analysis(
            True, [], {"candidate_windows_considered": 0, "rejected_sparse_windows": 0}))
        self.assertFalse(app._should_retry_sparse_vad_analysis(
            True, [{"start": 1, "end": 20}],
            {"candidate_windows_considered": 2, "rejected_sparse_windows": 2}))
        self.assertTrue(app._should_retry_sparse_vad_analysis(
            True, [], {"candidate_windows_considered": 2, "rejected_sparse_windows": 1}))

    def test_no_vad_retry_is_limited_to_empty_candidate_scans(self):
        sparse = {"rejected_sparse_windows": 40, "candidate_windows_considered": 40,
                  "normalized_words": 42}
        self.assertTrue(app._should_retry_sparse_vad_scan(True, [], sparse))
        self.assertFalse(app._should_retry_sparse_vad_scan(False, [], sparse))
        self.assertFalse(app._should_retry_sparse_vad_scan(True, [{"start": 10, "end": 30}], sparse))
        self.assertFalse(app._should_retry_sparse_vad_scan(True, [], {"candidate_windows_considered": 0,
                                                                       "normalized_words": 305}))
        self.assertFalse(app._should_retry_sparse_vad_scan(True, [], {"candidate_windows_considered": 0,
                                                                       "normalized_words": 5}))
        self.assertFalse(app._should_retry_sparse_vad_scan(True, [], {"candidate_windows_considered": 0,
                                                                       "normalized_words": 0,
                                                                       "rejected_sparse_windows": 0}))

    def test_sparse_vad_miss_with_real_candidate_windows_still_retries(self):
        sparse = {"rejected_sparse_windows": 40, "candidate_windows_considered": 40,
                  "normalized_words": 42}
        self.assertTrue(app._should_retry_sparse_vad_scan(True, [], sparse))

    def test_noisy_or_silent_pass_without_usable_windows_abstains_without_retry(self):
        # Segment filters leave no usable words when Whisper labels music/noise as
        # speech with high no-speech probability; there is no evidence for a retry.
        transcript = {"range_start": 0, "range_end": 90, "segments": [{
            "start": 10, "end": 16, "text": "bruit bruit bruit",
            "no_speech_prob": 0.96, "avg_logprob": -0.2, "compression_ratio": 1.1,
        }]}
        diagnostics = {}
        self.assertEqual(app.rank_candidate_excerpts(transcript, 20, 45, 8,
                                                     diagnostics=diagnostics), [])
        self.assertEqual(diagnostics["normalized_words"], 0)
        self.assertFalse(app._should_retry_sparse_vad_scan(True, [], diagnostics))

    def test_unfiltered_known_credit_parasite_cannot_qualify_as_retry_candidate(self):
        parasite = {"range_start": 0, "range_end": 60, "segments": [{
            "start": 0, "end": 30,
            "text": "Sous-titres réalisés par la communauté Amara.org",
        }]}
        diagnostics = {}
        candidates = app.rank_candidate_excerpts(parasite, 20, 45, 8,
                                                  diagnostics=diagnostics)
        self.assertEqual(candidates, [])
        self.assertFalse(app._prefer_vad_retry({"normalized_words": 0}, diagnostics, candidates))

    def test_fallback_is_selected_only_when_quality_filters_recover_words_and_a_clip(self):
        primary = {"normalized_words": 42}
        self.assertTrue(app._prefer_vad_retry(primary, {"normalized_words": 101}, [{"start": 20, "end": 40}]))
        self.assertFalse(app._prefer_vad_retry(primary, {"normalized_words": 101}, []))
        self.assertFalse(app._prefer_vad_retry(primary, {"normalized_words": 41}, [{"start": 20, "end": 40}]))

    def test_whisper_retry_keeps_absolute_source_timestamps(self):
        whisper_word = SimpleNamespace(start=0.25, end=0.55, word=" test", probability=0.9)
        whisper_segment = SimpleNamespace(start=0.2, end=0.7, text=" test", words=[whisper_word],
                                          avg_logprob=-0.2, no_speech_prob=0.01, compression_ratio=1.1)
        segments, words = app._collect_whisper_segments([whisper_segment], 100.0)
        self.assertEqual((segments[0]["start"], segments[0]["end"]), (100.2, 100.7))
        self.assertEqual((words[0]["start"], words[0]["end"]), (100.25, 100.55))

    def test_independent_candidate_cut_does_not_pad_into_next_utterance(self):
        path = app.ROOT / "validation" / "transcripts" / "source_5efb9fef42_small.json"
        transcript = json.loads(path.read_text(encoding="utf-8"))
        candidates = app.rank_candidate_excerpts(transcript, 20, 45, 8)
        candidate = next(c for c in candidates if 185 < c["start"] < 186)
        following = next(seg for seg in transcript["segments"]
                         if abs(float(seg["start"]) - 223.81) < 0.02)
        self.assertLessEqual(candidate["end"], float(following["start"]))

    def test_two_production_modes_use_distinct_segmentation_paths(self):
        transcript = make_transcript(28)
        independent = app.rank_candidate_excerpts(transcript, 20, 45, 8)
        episodes = app.build_story_parts(transcript, 4, transcript["range_end"])
        self.assertTrue(independent)
        self.assertTrue(episodes)
        self.assertIn("scores", independent[0])
        self.assertNotIn("scores", episodes[0])
        self.assertAlmostEqual(episodes[0]["start"], 0.0)
        self.assertAlmostEqual(episodes[-1]["end"], transcript["range_end"])


class NarrativeSequenceTests(unittest.TestCase):
    def test_anecdote_and_question_answer_stay_together_until_conclusion(self):
        transcript = {"range_start": 0, "range_end": 60, "segments": [
            {"start": 0, "end": 2, "text": "Je vais vous raconter une histoire."},
            {"start": 2.4, "end": 5, "text": "Hier soir, j'ai raté mon train."},
            {"start": 5.3, "end": 8, "text": "Le contrôleur m'a demandé mon billet."},
            {"start": 8.2, "end": 9, "text": "Vous aviez votre ticket ?"},
            {"start": 9.4, "end": 10, "text": "Oui, dans l'autre poche."},
            {"start": 10.4, "end": 13, "text": "Finalement je suis monté dans le bon train."},
            {"start": 13.2, "end": 15, "text": "Tout le wagon a applaudi, voilà la chute."},
            {"start": 30, "end": 33, "text": "Passons maintenant à la recette du gâteau."},
            {"start": 34, "end": 37, "text": "Ajoutez deux œufs, du sucre et de la farine."},
        ]}
        sequences = detect_narrative_sequences(transcript)
        self.assertEqual(len(sequences), 2)
        self.assertAlmostEqual(sequences[0]["end"], 30)
        self.assertIn("Tout le wagon a applaudi", sequences[0]["text"])
        self.assertNotIn("Vous aviez votre ticket", sequences[1]["text"])

    def test_two_independent_vignettes_are_separate_elementary_sequences(self):
        transcript = {"range_start": 0, "range_end": 45, "segments": [
            {"start": 0, "end": 2, "text": "Bonjour à tous."},
            {"start": 3, "end": 8, "text": "La première histoire parle d'un train perdu."},
            {"start": 25, "end": 27, "text": "Bonjour à tous."},
            {"start": 28, "end": 34, "text": "Cette histoire parle d'une recette au chocolat."},
        ]}
        sequences = detect_narrative_sequences(transcript)
        self.assertEqual(len(sequences), 2)
        self.assertEqual(sequences[0]["end"], sequences[1]["start"])
        self.assertIn("train", sequences[0]["text"])
        self.assertIn("recette", sequences[1]["text"])

    def test_visual_cut_alone_does_not_create_a_narrative_boundary(self):
        transcript = {"range_start": 0, "range_end": 20, "segments": [
            {"start": 0, "end": 5, "text": "Nous continuons notre histoire dans le même jeu."},
            {"start": 5.5, "end": 10, "text": "Dans ce jeu nous continuons la même histoire."},
        ]}
        self.assertEqual(len(detect_narrative_sequences(transcript, visual_transitions=[5.5])), 1)

    def test_visual_cut_can_corroborate_a_strong_topic_shift(self):
        transcript = {"range_start": 0, "range_end": 20, "segments": [
            {"start": 0, "end": 5, "text": "Le voilier traverse la mer calme et les vagues brillent."},
            {"start": 5.5, "end": 10, "text": "Le réseau informatique protège les données des clients."},
        ]}
        self.assertEqual(len(detect_narrative_sequences(transcript, visual_transitions=[5.5])), 2)

    def test_alternating_format_intros_keep_both_halves_of_a_pair_together(self):
        transcript = {"range_start": 0, "range_end": 50, "segments": [
            {"start": 0, "end": 1, "text": "Dans les pubs."},
            {"start": 1.2, "end": 8, "text": "Le produit promet un résultat incroyable."},
            {"start": 10, "end": 11, "text": "Dans la vie."},
            {"start": 11.2, "end": 20, "text": "La situation réelle tourne mal et tout le monde rit."},
            {"start": 25, "end": 26, "text": "Dans les pubs."},
            {"start": 26.2, "end": 32, "text": "La deuxième offre promet aussi un résultat incroyable."},
            {"start": 35, "end": 36, "text": "Dans la vie."},
            {"start": 36.2, "end": 44, "text": "Cette fois le résultat réel surprend toute la famille."},
        ]}
        sequences = detect_narrative_sequences(transcript)
        self.assertEqual([(s["start"], s["end"]) for s in sequences], [(0.0, 25.0), (25.0, 50.0)])

    def test_real_pubs_video_uses_sequence_boundaries_and_sparse_video_keeps_baseline(self):
        root = app.ROOT / "validation"
        pubs = json.loads((root / "transcripts" / "source_5efb9fef42_small.json").read_text(encoding="utf-8"))
        sequence_starts = {round(s["start"], 2) for s in detect_narrative_sequences(pubs)}
        parts = app.build_story_parts(pubs, 4, pubs["range_end"])
        self.assertEqual(len(parts), 4)
        self.assertEqual([round(p["end"], 2) for p in parts[:-1]], [105.43, 230.23, 388.30])
        self.assertLess(parts[0]["end"], 150)
        self.assertTrue(all(round(p["start"], 2) in sequence_starts for p in parts[1:]))
        self.assertTrue(all(a["end"] == b["start"] for a, b in zip(parts, parts[1:])))

        sparse = json.loads((root / "transcripts" / "source_57e0fbd52b_small.json").read_text(encoding="utf-8"))
        baseline = json.loads((root / "episode_segmentation_baseline_20261111.json").read_text(encoding="utf-8"))
        expected = baseline["source_57e0fbd52b"]["parts"]
        actual = app.build_story_parts(sparse, 4, sparse["range_end"])
        self.assertEqual([(p["start"], p["end"]) for p in actual],
                         [(p["start"], p["end"]) for p in expected])


if __name__ == "__main__":
    unittest.main()
