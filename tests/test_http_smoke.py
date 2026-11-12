import threading
import json
import unittest
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import app


class LocalHttpSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.demo_sid = None
        for session in app.load_sessions_index().get("sessions", []):
            sid = str(session.get("id", "default"))
            path = app.studio_path(sid)
            if not path.exists():
                continue
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            source = state.get("source")
            if source and Path(source).exists():
                cls.demo_sid = sid
                break

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def get(self, path, headers=None):
        with urlopen(Request(self.base + path, headers=headers or {}), timeout=10) as response:
            return response.status, response.headers, response.read()

    def test_dashboard_and_studio_render(self):
        status, _, dashboard = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Clip Factory".encode(), dashboard)
        self.assertIn(b"min_duration", dashboard)
        self.assertIn("Statistiques".encode(), dashboard)
        self.assertIn("Bibliothèque".encode(), dashboard)
        if not self.demo_sid:
            self.skipTest("Aucune session locale avec une vidéo source n'est disponible.")
        status, _, studio = self.get("/studio?sid=" + self.demo_sid)
        self.assertEqual(status, 200)
        self.assertIn("Trouver les meilleurs extraits indépendants".encode(), studio)
        self.assertIn("Découper en épisodes qui se suivent".encode(), studio)
        self.assertIn(b"name='campaign_id'", studio)
        self.assertEqual(studio.count(b"name=\"campaign_id\""), 1)

    def test_reference_review_view_is_separate_from_engine_results(self):
        if not self.demo_sid:
            self.skipTest("Aucune session locale avec une vidéo source n'est disponible.")
        status, _, review = self.get("/reference-review?sid=" + self.demo_sid)
        self.assertEqual(status, 200)
        self.assertIn("Jeu de référence indépendant".encode(), review)
        self.assertIn("Intérêt narratif".encode(), review)
        self.assertNotIn("Passages proposés".encode(), review)

    def test_library_lists_local_media_and_sessions(self):
        status, _, library = self.get("/library")
        self.assertEqual(status, 200)
        self.assertIn("Vidéos sources".encode(), library)
        self.assertIn("Exports MP4".encode(), library)
        self.assertIn("Sessions vidéo".encode(), library)

    def test_preview_supports_http_byte_ranges(self):
        if not self.demo_sid:
            self.skipTest("Aucune source n'est liée à la session de démonstration locale.")
        status, headers, payload = self.get("/source-preview?sid=" + self.demo_sid, {"Range": "bytes=0-127"})
        self.assertEqual(status, 206)
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(len(payload), 128)

    def test_data_exports_are_available(self):
        status, _, exported = self.get("/export")
        self.assertEqual(status, 200)
        self.assertIn(b'"campaigns"', exported)
        status, headers, csv = self.get("/csv")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers.get("Content-Type", ""))
        self.assertIn("Titre".encode(), csv)

    def test_new_isolated_atelier_session_generates_episodes_from_saved_transcript(self):
        transcript_path = app.ROOT / "validation" / "transcripts" / "source_5efb9fef42_small.json"
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        source = app.ROOT / "media" / "sources" / "source_5efb9fef42.mp4"
        previous_session = app.ACTIVE_SESSION_ID.get()
        with tempfile.TemporaryDirectory() as temp_root:
            isolated_sessions = Path(temp_root) / "sessions"
            isolated_index = Path(temp_root) / "index.json"
            with patch.object(app, "SESSIONS_DIR", isolated_sessions), \
                 patch.object(app, "SESSIONS_INDEX", isolated_index):
                sid = app.create_session()
                app.ACTIVE_SESSION_ID.set(sid)
                state = app.empty_studio()
                state.update({"source": str(source), "duration": transcript["range_end"],
                              "transcript": transcript, "analysis_mode": "story_parts",
                              "parts_count": 4, "scan_full": True})
                app.save_studio(state)
                payload = b"count=4"
                request = Request(self.base + "/split-parts?sid=" + sid, data=payload,
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
                with urlopen(request, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    page = response.read()
                app.ACTIVE_SESSION_ID.set(sid)
                saved = app.load_studio()
                self.assertEqual(saved["analysis_mode"], "story_parts")
                self.assertEqual(len(saved["parts"]), 4)
                self.assertEqual(round(saved["parts"][0]["end"], 2), 105.43)
                self.assertIn("séquences narratives regroupées".encode(), page)
        app.ACTIVE_SESSION_ID.set(previous_session)


if __name__ == "__main__":
    unittest.main()
