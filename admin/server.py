"""LAN-restricted lyric editor with independent credentials.

Socket is bound to loopback and the Mac's home-LAN interface only.
Localhost keeps the original behavior; LAN clients require a separate password.
There is no exposure through the public lyrics API / Debian reverse proxy.
"""
from __future__ import annotations

import hashlib
import ipaddress
import hmac
import logging
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time

from flask import Flask, abort, jsonify, request, send_from_directory
from waitress import serve

from api.lyrics import _local_candidate
from mod import confirmed_store, searchx

LOG = logging.getLogger(__name__)
PORT = 28886
LAN_HOST = "192.168.3.7"
LAN_NETWORK = ipaddress.ip_network("192.168.3.0/24")
ALLOWED_HOSTS = {
    f"127.0.0.1:{PORT}", f"localhost:{PORT}", "localhost", f"{LAN_HOST}:{PORT}",
}
_ALLOWED_ORIGINS = {f"http://{name}" for name in ALLOWED_HOSTS if ":" in name}
_AUTH_FAILURES: dict[str, tuple[int, float]] = {}
_AUTH_LOCK = threading.Lock()
MAX_ITEMS = 350
AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".aac", ".wav",
    ".ape", ".wma", ".dsf", ".aiff", ".m4b",
}
CSRF = secrets.token_urlsafe(36)
STATIC_ROOT = Path(__file__).resolve().parent / "static"
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 250_000


def _root() -> Path:
    root = os.environ.get("HOME_SERVER_MUSIC_ROOT", "")
    if not root:
        raise ValueError("The music root is not configured")
    return Path(root).resolve()


def _resolve(relative: str, *, audio: bool = False, directory: bool = False) -> Path:
    if not isinstance(relative, str) or len(relative) > 1024:
        raise ValueError("Invalid relative music path")
    if relative.startswith(("/", "\\")) or "\x00" in relative:
        raise ValueError("Absolute paths and NUL are not permitted")
    root = _root()
    target = (root / relative).resolve()
    try:
        normalized = target.relative_to(root)
    except ValueError:
        raise ValueError("Path escapes the music root") from None
    if audio and (target.suffix.lower() not in AUDIO_EXTENSIONS or not target.is_file()):
        raise ValueError("Only readable audio tracks may be edited")
    if directory and not target.is_dir():
        raise ValueError("Not a readable music directory")
    return target


def _relative(path: Path) -> str:
    return path.relative_to(_root()).as_posix()


def _identify(path: Path) -> dict:
    """Read metadata of one selected track, not a full-library scan."""
    title = re.sub(r"^\d{1,3}[\s.\-_]+", "", path.stem)
    album = path.parent.name
    artist = path.parent.parent.name if path.parent.parent != _root() else ""
    duration = ""
    try:
        from mutagen import File
        audio = File(str(path), easy=True)
        if audio is not None:
            def first(tag, default):
                raw = audio.get(tag, [])
                return str(raw[0])[:256] if raw else default

            title = first("title", title)
            artist = first("artist", artist)
            album = first("album", album)
            if audio.info and audio.info.length:
                duration = str(round(float(audio.info.length), 3))
    except Exception as exc:
        LOG.info("One-track audio metadata unavailable: %s", type(exc).__name__)
    return {"title": title[:256], "artist": artist[:256],
            "album": album[:256], "duration": duration}


def _current(relative: str) -> dict:
    path = _resolve(relative, audio=True)
    metadata = _identify(path)
    virtual = "/music/" + _relative(path)
    original = _local_candidate(str(path), metadata["title"], metadata["artist"], metadata["album"])
    correction = confirmed_store.find(
        path=virtual, title=metadata["title"], artist=metadata["artist"],
        album=metadata["album"], duration=metadata["duration"])
    active = correction or original
    lyric = (active or {}).get("lyrics", "")
    result = {
        "path": _relative(path),
        "name": path.name,
        "metadata": metadata,
        "status": "corrected" if correction else (
            "sidecar" if original and original["source"] == "sidecar_default" else
            "embedded" if original else "missing"),
        "active": lyric,
        "active_sha256": hashlib.sha256(lyric.encode("utf-8")).hexdigest(),
        "original": (original or {}).get("lyrics", ""),
        "original_source": (original or {}).get("source", ""),
        "correction_id": correction["id"] if correction else None,
    }
    from admin import sidecar_export
    result["sidecar_export"] = sidecar_export.status(result["path"])
    return result


def _client_is_loopback() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1")


def _allow_client() -> bool:
    try:
        address = ipaddress.ip_address(request.remote_addr or "")
        return address.is_loopback or address in LAN_NETWORK
    except ValueError:
        return False


def _authenticate_lan():
    """Browser-native Basic Auth only on LAN; localhost remains unchanged."""
    peer = request.remote_addr or ""
    password = os.environ.get("LRCAPI_EDITOR_PASSWORD", "")
    if not password or len(password) < 24:
        LOG.error("LAN editor password is absent or weak; rejecting LAN request")
        return jsonify({"error": "LAN editor is not configured"}), 503
    with _AUTH_LOCK:
        count, expires = _AUTH_FAILURES.get(peer, (0, 0.0))
        if expires <= time.monotonic():
            count = 0
        if count >= 12:
            return jsonify({"error": "Too many authentication attempts"}), 429

    auth = request.authorization
    valid = bool(auth and auth.type == "basic" and
                 hmac.compare_digest(auth.username or "", "lyrics") and
                 hmac.compare_digest(auth.password or "", password))
    if valid:
        with _AUTH_LOCK:
            _AUTH_FAILURES.pop(peer, None)
        return None

    with _AUTH_LOCK:
        existing, expiry = _AUTH_FAILURES.get(peer, (0, 0))
        if expiry <= time.monotonic():
            existing = 0
        _AUTH_FAILURES[peer] = (existing + 1, time.monotonic() + 120)
    response = jsonify({"error": "LAN authentication required"})
    response.status_code = 401
    response.headers["WWW-Authenticate"] = 'Basic realm="Home Lyrics Editor", charset="UTF-8"'
    return response


@app.before_request
def security_gate():
    # Neither Tailscale nor arbitrary interfaces are listening. Check source
    # anyway to fail closed if an operator later changes the bind address.
    if not _allow_client():
        return jsonify({"error": "home LAN only"}), 403
    if request.host.lower() not in ALLOWED_HOSTS:
        return jsonify({"error": "untrusted Host"}), 403
    if request.method not in ("GET", "HEAD", "POST"):
        return jsonify({"error": "not allowed"}), 405
    if not _client_is_loopback():
        forbidden = _authenticate_lan()
        if forbidden is not None:
            return forbidden
    if request.method == "POST":
        if not request.is_json:
            return jsonify({"error": "JSON only"}), 415
        supplied = request.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, CSRF):
            return jsonify({"error": "CSRF verification failed"}), 403
        origin = request.headers.get("Origin", "").lower()
        if origin and origin not in _ALLOWED_ORIGINS:
            return jsonify({"error": "foreign Origin"}), 403


@app.after_request
def protective_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return response


@app.get("/")
def index():
    return send_from_directory(STATIC_ROOT, "index.html")


@app.get("/style.css")
def style():
    return send_from_directory(STATIC_ROOT, "style.css", mimetype="text/css")


@app.get("/theme.js")
def theme_script():
    return send_from_directory(STATIC_ROOT, "theme.js", mimetype="text/javascript")


@app.get("/app.js")
def script():
    return send_from_directory(STATIC_ROOT, "app.js", mimetype="text/javascript")


@app.get("/api/session")
def session():
    return jsonify({"csrf": CSRF, "mode": "lan-authenticated" if not _client_is_loopback() else "loopback",
                    "name": "歌词纠错台", "writes_music": False})


@app.get("/api/browse")
def browse():
    relative = request.args.get("path", "")
    term = request.args.get("q", "").strip().casefold()[:100]
    try:
        folder = _resolve(relative, directory=True)
        entries = []
        visited = 0
        with os.scandir(folder) as scan:
            for entry in scan:
                visited += 1
                if visited > 5000:
                    break
                if entry.name.startswith(".") or entry.name in ("@eaDir",):
                    continue
                if term and term not in entry.name.casefold():
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_audio = (entry.is_file(follow_symlinks=False)
                                and Path(entry.name).suffix.lower() in AUDIO_EXTENSIONS)
                except OSError:
                    continue
                if not (is_dir or is_audio):
                    continue
                entries.append({"name": entry.name, "path": _relative(Path(entry.path)),
                                "kind": "folder" if is_dir else "track"})
        entries.sort(key=lambda e: (e["kind"] != "folder", e["name"].casefold()))
        return jsonify({"path": _relative(folder), "items": entries[:MAX_ITEMS],
                        "truncated": len(entries) > MAX_ITEMS or visited > 5000})
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/track")
def track():
    try:
        return jsonify(_current(request.args.get("path", "")))
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/history")
def history():
    try:
        song = _current(request.args.get("path", ""))
        m = song["metadata"]
        values = confirmed_store.history(
            path="/music/" + song["path"], title=m["title"],
            artist=m["artist"], album=m["album"], duration=m["duration"])
        return jsonify(values)
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/candidates")
def candidates():
    try:
        song = _current(request.args.get("path", ""))
        m = song["metadata"]
        if not m["title"] or not m["artist"]:
            return jsonify({"error": "Song title and artist needed to search sources"}), 422
        results = searchx.search_all(m["title"], m["artist"], m["album"],
                                     timeout=6, duration=m["duration"] or None)
        # Returning a candidate does not mark it confirmed. Saving is always manual.
        return jsonify({"items": results[:8]})
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


def _post_song():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")
    relative = payload.get("path", "")
    if not isinstance(relative, str):
        raise ValueError("Track path required")
    song = _current(relative)
    if payload.get("expected_sha256") != song["active_sha256"]:
        raise RuntimeError("Lyrics have changed. Reload the song before saving.")
    return payload, song


@app.post("/api/save")
def save():
    try:
        payload, song = _post_song()
        lyric = payload.get("lyrics")
        if not isinstance(lyric, str) or not lyric.strip() or len(lyric) > 200_000:
            return jsonify({"error": "Lyric content must be 1–200000 characters"}), 422
        if "\x00" in lyric:
            return jsonify({"error": "Invalid lyric text"}), 422
        meta = song["metadata"]
        new_id = confirmed_store.confirm(
            lyrics=lyric, path="/music/" + song["path"],
            title=meta["title"], artist=meta["artist"], album=meta["album"],
            duration=meta["duration"], source="local_editor", reviewer="owner")
        backup_ok = True
        try:
            confirmed_store.snapshot()
        except (ValueError, OSError) as exc:
            LOG.error("SQLite snapshot failed: %s", type(exc).__name__)
            backup_ok = False
        return jsonify({"ok": True, "id": new_id, "writes_music": False,
                        "backup_ok": backup_ok})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/revoke")
def revoke():
    try:
        _, song = _post_song()
        if not song["correction_id"]:
            return jsonify({"error": "There is no correction to revoke"}), 409
        if song.get("sidecar_export") and song["sidecar_export"]["status"] != "restored":
            return jsonify({"error": "Restore the exported .lrc before revoking the SQLite correction"}), 409
        meta = song["metadata"]
        confirmed_store.revoke(
            path="/music/" + song["path"],
            title=meta["title"], artist=meta["artist"],
            album=meta["album"], duration=meta["duration"], reviewer="owner")
        backup_ok = True
        try:
            confirmed_store.snapshot()
        except (ValueError, OSError) as exc:
            LOG.error("SQLite snapshot failed: %s", type(exc).__name__)
            backup_ok = False
        return jsonify({"ok": True, "writes_music": False, "backup_ok": backup_ok})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400



@app.post("/api/export")
def export_to_sidecar():
    try:
        _, song = _post_song()
        from admin import sidecar_export
        return jsonify(sidecar_export.export(song["path"]))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/restore")
def restore_sidecar():
    try:
        _, song = _post_song()
        from admin import sidecar_export
        return jsonify(sidecar_export.restore(song["path"]))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400

def main():
    if not os.environ.get("HOME_SERVER_MUSIC_ROOT"):
        raise RuntimeError("Music root is required")
    if len(os.environ.get("LRCAPI_EDITOR_PASSWORD", "")) < 24:
        raise RuntimeError("LAN editor password is not configured")
    logging.basicConfig(level=logging.INFO)
    LOG.info("Starting editor on localhost + home LAN interface, port %d", PORT)
    serve(app, listen=f"127.0.0.1:{PORT} {LAN_HOST}:{PORT}", threads=3,
          connection_limit=32, channel_timeout=15, max_request_body_size=250_000)


if __name__ == "__main__":
    sys.exit(main())
