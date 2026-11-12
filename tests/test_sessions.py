import json
import tempfile
import unittest
from pathlib import Path

import app


class SessionPersistenceTests(unittest.TestCase):
    def test_create_close_reopen_and_persist_session(self):
        old = (app.SESSIONS_DIR, app.SESSIONS_INDEX, app.STUDIO)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app.SESSIONS_DIR = root / "sessions"
            app.SESSIONS_INDEX = app.SESSIONS_DIR / "index.json"
            app.STUDIO = root / "studio.json"
            try:
                sid = app.create_session()
                data = json.loads(app.SESSIONS_INDEX.read_text(encoding="utf-8"))
                session = next(item for item in data["sessions"] if item["id"] == sid)
                self.assertTrue(session["open"])
                self.assertTrue(app.studio_path(sid).exists())
                app.close_session_tab(sid)
                self.assertFalse(next(item for item in app.load_sessions_index()["sessions"] if item["id"] == sid)["open"])
                self.assertTrue(app.open_session_tab(sid))
                self.assertTrue(next(item for item in app.load_sessions_index()["sessions"] if item["id"] == sid)["open"])
            finally:
                app.SESSIONS_DIR, app.SESSIONS_INDEX, app.STUDIO = old

    def test_loading_data_does_not_insert_unrequested_campaigns(self):
        old = app.DATA
        with tempfile.TemporaryDirectory() as temp:
            app.DATA = Path(temp) / "data.json"
            original = {"settings": {"monthly_goal": 500}, "campaigns": [], "clips": []}
            app.DATA.write_text(json.dumps(original), encoding="utf-8")
            try:
                loaded = app.load_data()
                self.assertEqual(loaded["campaigns"], [])
                self.assertEqual(json.loads(app.DATA.read_text(encoding="utf-8"))["campaigns"], [])
            finally:
                app.DATA = old


if __name__ == "__main__":
    unittest.main()
