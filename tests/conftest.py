import os
import pytest

# Set defaults before any test module imports the application.
os.environ.setdefault("API_AUTH", "offline-test-key")
os.environ.setdefault("LRCAPI_READ_ONLY", "1")


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("API_AUTH", "offline-test-key")
    monkeypatch.setenv("LRCAPI_DB_PATH", str(tmp_path / "private" / "confirmed.sqlite3"))
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setenv("HOME_SERVER_MUSIC_ROOT", str(music))
    yield


@pytest.fixture
def client():
    from app import app, v1_bp
    if "v1" not in app.blueprints:
        app.register_blueprint(v1_bp)
    return app.test_client()
