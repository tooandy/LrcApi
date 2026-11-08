"""Focused full-app compatibility tests; no network and no NAS writes."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["API_AUTH"] = "test-auth-native"
os.environ["LRCAPI_READ_ONLY"] = "1"

from app import app, v1_bp
from api import __import__

if "v1" not in app.blueprints:
    app.register_blueprint(v1_bp)


class NativeReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_old_routes_and_auth(self):
        self.assertEqual(self.client.get("/time").status_code, 200)
        self.assertEqual(self.client.get("/src").status_code, 200)
        self.assertIn(self.client.get("/lyrics?title=example").status_code, (401, 403))
        self.assertIn(self.client.get("/jsonapi?title=example").status_code, (401, 403))

    def test_readonly_denies_writes(self):
        headers = {"Authorization": "test-auth-native"}
        self.assertEqual(self.client.post("/tag", headers=headers, json={"path": "/tmp/example.mp3"}).status_code, 403)
        self.assertEqual(self.client.post("/confirm", headers=headers, json={"path": "/tmp/example.mp3"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/file/download", headers=headers, json={}).status_code, 403)

    def test_music_path_maps_and_rejects_traversal(self):
        from api.lyrics import local_music_path
        with tempfile.TemporaryDirectory() as music:
            with patch.dict(os.environ, {"HOME_SERVER_MUSIC_ROOT": music}):
                self.assertEqual(local_music_path("/music/Artist/track.mp3"), str(Path(music) / "Artist" / "track.mp3"))
                self.assertEqual(local_music_path("/mnt/v2/Music/Artist/track.mp3"), str(Path(music) / "Artist" / "track.mp3"))
                self.assertEqual(local_music_path("/music/../etc/passwd"), "")
                self.assertEqual(local_music_path("/etc/passwd"), "")

    def test_sidecar_lrc_without_upstream(self):
        with tempfile.TemporaryDirectory() as music:
            path = Path(music) / "Test.mp3"
            path.write_bytes(b"fake-audio")
            (Path(music) / "Test.lrc").write_text("[00:01.20]Hello world\n")
            with patch.dict(os.environ, {"HOME_SERVER_MUSIC_ROOT": music}), patch("api.lyrics.searchx.search_all") as remote:
                result = self.client.get("/lyrics?path=/music/Test.mp3",headers={"Authorization": "test-auth-native"})
            self.assertEqual(result.status_code, 200)
            self.assertIn("Hello world", result.get_data(as_text=True))
            remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
