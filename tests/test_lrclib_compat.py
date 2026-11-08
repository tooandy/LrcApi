import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ReadOnlyCompatTests(unittest.TestCase):
    """Load the adapter with an isolated Flask app (no production startup)."""

    @classmethod
    def setUpClass(cls):
        from flask import Flask
        cls.flask = Flask(__name__)
        api = types.ModuleType("api")
        api.__path__ = [str(Path(__file__).resolve().parents[1] / "api")]
        api.app = cls.flask
        sys.modules["api"] = api
        mod = types.ModuleType("mod")
        mod.__path__ = []
        sys.modules["mod"] = mod
        auth = types.ModuleType("mod.auth")
        auth.require_auth_decorator = lambda permission: (lambda fn: fn)
        sys.modules["mod.auth"] = auth
        spec = importlib.util.spec_from_file_location("api.lrclib_compat",
            Path(__file__).resolve().parents[1] / "api" / "lrclib_compat.py")
        compat = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = compat
        spec.loader.exec_module(compat)
        cls.compat = compat

    def setUp(self):
        self.client = self.flask.test_client()
        self.compat._cache.clear()
        self.compat._cooldown_until = 0

    def test_search_response_and_cache(self):
        response = Mock(status_code=200, content=b'[{"id":1}]',
                        headers={}, json=lambda: [{"id": 1}])
        with patch.object(self.compat.requests, "get", return_value=response) as fake:
            r1 = self.client.get("/api/search?track_name=Test")
            r2 = self.client.get("/api/search?track_name=Test")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.json[0]["id"], 1)
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(fake.call_args.kwargs["timeout"], (3, 10))

    def test_get_requires_signature(self):
        self.assertEqual(self.client.get("/api/get?track_name=Test").status_code, 400)
        self.assertEqual(self.client.get("/api/search").status_code, 400)

    def test_429_respects_retry_after_without_retry(self):
        response = Mock(status_code=429, headers={"Retry-After": "20"})
        with patch.object(self.compat.requests, "get", return_value=response) as fake:
            first = self.client.get("/api/search?q=test")
            second = self.client.get("/api/search?q=other")
        self.assertEqual(first.status_code, 429)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(fake.call_count, 1)

    def test_connection_timeout_degrades_gracefully(self):
        with patch.object(self.compat.requests, "get",
                          side_effect=self.compat.requests.Timeout):
            result = self.client.get("/api/search?q=test")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.headers["Retry-After"], "900")

    def test_unknown_response_and_invalid_id(self):
        response = Mock(status_code=200, content=b"not json", headers={})
        response.json.side_effect = ValueError
        with patch.object(self.compat.requests, "get", return_value=response):
            self.assertEqual(self.client.get("/api/search?q=test").status_code, 502)
        self.assertEqual(self.client.get("/api/get/0").status_code, 400)
        self.assertEqual(self.client.get("/api/get/42?x=1").status_code, 400)


if __name__ == "__main__":
    unittest.main()
