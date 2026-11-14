"""Small, authenticated, read-only replacement for the old /cover endpoint.

Only fetches HTTPS image/jpeg from QQ Music's fixed CDN after matching a
song, album, or singer. No arbitrary URLs, proxies or unsafe redirects.
"""
from __future__ import annotations

import os

import requests
from flask import Response, abort, jsonify, request

from . import app, v1_bp
from mod.auth import require_auth_decorator
from mod.searchx import qqmusic

_MAX_IMAGE = 2_000_000
_HEADERS = {"User-Agent": "HomeLrcAPI/1.0 (private music artwork)"}


def _artwork() -> Response:
    if os.environ.get("LRCAPI_QQMUSIC_ENABLED", "1") != "1":
        return jsonify({"error": "QQ Music source disabled"}), 503
    raw = {key: request.args.get(key, "")
           for key in ("title", "artist", "album")}
    if any(not isinstance(value, str) or len(value) > 256 for value in raw.values()):
        abort(400, description="Invalid music metadata")
    title, artist, album = (raw[key].strip() for key in ("title", "artist", "album"))
    kind = request.view_args.get("kind", "album") if request.view_args else "album"
    if kind not in ("album", "music", "artist"):
        abort(404)
    if not (title or artist or album):
        abort(400, description="Music metadata required")
    if kind == "artist" or (not album and not title):
        kind = "artist"
        mid = qqmusic.cover_mid(artist=artist, kind=kind)
    else:
        mid = qqmusic.cover_mid(title=title, artist=artist, album=album,
                                kind="album")
        kind = "album"
    url = qqmusic.cover_url(mid, kind=kind)
    if not url:
        abort(404, description="QQ Music artwork not found")
    # url can only be constructed by cover_url() with a validated 14-char
    # QQ MID and the hard-coded y.gtimg.cn HTTPS origin.
    try:
        upstream = requests.get(url, headers=_HEADERS, timeout=(2, 4),
                                stream=True, allow_redirects=False)
        try:
            if upstream.status_code != 200:
                abort(404, description="QQ Music artwork unavailable")
            if not upstream.headers.get("Content-Type", "").lower().startswith("image/jpeg"):
                abort(502, description="Artwork is not a JPEG")
            length = upstream.headers.get("Content-Length")
            if length:
                try:
                    if int(length) > _MAX_IMAGE:
                        abort(502, description="Artwork exceeds size limit")
                except ValueError:
                    abort(502, description="Invalid artwork size")
            chunks = []
            size = 0
            for part in upstream.iter_content(chunk_size=16_384):
                size += len(part)
                if size > _MAX_IMAGE:
                    abort(502, description="Artwork exceeds size limit")
                chunks.append(part)
            payload = b"".join(chunks)
        finally:
            upstream.close()
        if len(payload) < 4 or not payload.startswith(b"\xff\xd8\xff"):
            abort(502, description="Invalid JPEG artwork")
        return Response(payload, status=200, mimetype="image/jpeg")
    except requests.RequestException:
        abort(502, description="QQ Music artwork source unavailable")


@app.get("/cover")
@v1_bp.get("/cover/<kind>")
@require_auth_decorator("r")
def qq_cover(kind="album"):
    return _artwork()
