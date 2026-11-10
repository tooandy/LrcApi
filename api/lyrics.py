"""Read-only lyrics: manual SQLite correction > local .lrc > audio tags > providers."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from flask import abort, jsonify, request

from . import app, v1_bp
from mod.auth import require_auth_decorator
from mod import confirmed_store, lrc, searchx, tag

LOG = logging.getLogger(__name__)


def local_music_path(path: str) -> str:
    if not path:
        return ""
    root = os.environ.get("HOME_SERVER_MUSIC_ROOT")
    if not root:
        return ""  # Never allow access to arbitrary paths without the root.
    root = os.path.realpath(root)
    for prefix in ("/music", "/mnt/v2/Music", root):
        if path == prefix or path.startswith(prefix + "/"):
            relative = os.path.relpath(path, prefix)
            resolved = os.path.realpath(os.path.join(root, relative))
            if resolved != root and resolved.startswith(root + os.sep):
                return resolved
    return ""


def _local_candidate(path: str, title: str, artist: str, album: str) -> dict | None:
    if not path or not os.path.isfile(path):
        return None
    sidecar = Path(os.path.splitext(path)[0] + ".lrc")
    music_root = os.environ.get("HOME_SERVER_MUSIC_ROOT", "")
    # A sidecar symlink must never escape the approved music tree.
    if sidecar.is_symlink() and (not music_root or not sidecar.resolve().is_relative_to(Path(music_root).resolve())):
        LOG.warning("Ignoring sidecar symlink outside the music root")
        sidecar = Path("")
    if sidecar.is_file() and sidecar.stat().st_size <= 200_000:
        for encoding in ("utf-8-sig", "gbk"):
            try:
                lyric = sidecar.read_text(encoding=encoding)
                if lyric.strip():
                    return _candidate(title, artist, album, lyric, "sidecar_default")
            except UnicodeError:
                pass
            except OSError:
                break
    try:
        embedded = tag.read(path).get("lyrics")
        if isinstance(embedded, str) and embedded.strip():
            return _candidate(title, artist, album, embedded, "embedded_default")
    except Exception as exc:
        LOG.info("Embedded lyric unavailable: %s", type(exc).__name__)
    return None


def _candidate(title, artist, album, text, source):
    return {"id": hashlib.sha256(text.encode("utf-8")).hexdigest()[:24],
            "title": title, "artist": artist, "album": album,
            "lyrics": lrc.standard(text), "source": source, "verified": False,
            "assumed_correct": True}


def _query() -> list[dict]:
    if not request.args:
        abort(400, description="Track metadata is required")
    raw_path = request.args.get("path", "")
    path = local_music_path(raw_path)
    title = request.args.get("title", "").strip()[:256]
    artist = request.args.get("artist", "").strip()[:256]
    album = request.args.get("album", "").strip()[:256]
    duration = request.args.get("duration", "")
    if raw_path and not path:
        abort(403, description="Invalid path")
    confirmed = confirmed_store.find(
        path=raw_path, title=title, artist=artist, album=album, duration=duration)
    if confirmed:
        return [confirmed]
    local = _local_candidate(path, title, artist, album)
    if local:
        return [local]
    if not title:
        return []
    return searchx.search_all(title, artist, album, timeout=7, duration=duration or None)


@app.get("/lyrics")
@v1_bp.get("/lyrics/single")
@require_auth_decorator(permission="r")
def lyrics():
    results = _query()
    if not results or not results[0].get("lyrics"):
        return "Lyrics not found.", 404
    return results[0]["lyrics"]


@app.get("/jsonapi")
@v1_bp.get("/lyrics/advance")
@require_auth_decorator(permission="r")
def lrc_json():
    return jsonify(_query())
