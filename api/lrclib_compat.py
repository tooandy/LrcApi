"""Read-only LRCLIB API compatibility for clients that support Authorization headers.

The official LRCLIB remains the source of record. No publish/challenge APIs,
synthetic IDs, music-file writes, or unbounded upstream retries are provided.
"""
import logging
import math
import threading
import time
from collections import OrderedDict

import requests
from flask import Response, jsonify, request

from . import app
from mod.auth import require_auth_decorator

LOG = logging.getLogger(__name__)
BASE = "https://lrclib.net"
USER_AGENT = "HomeServer-LrcApi/0.1 (https://github.com/tooandy/LrcApi)"
ALLOWED = {"track_name", "artist_name", "album_name", "duration", "q"}
CACHE_LIMIT = 256
MAX_RESPONSE = 2_000_000
_lock = threading.RLock()
_cache = OrderedDict()
_cooldown_until = 0.0
_cooldown_status = 429


def _json_error(message, status, retry_after=None):
    response = jsonify({"message": message})
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _request(path, params):
    global _cooldown_until, _cooldown_status
    now = time.monotonic()
    key = (path, tuple(sorted(params.items())))
    with _lock:
        entry = _cache.get(key)
        if entry is not None:
            expiry, code, data = entry
            if expiry > now:
                _cache.move_to_end(key)
                return Response(data, status=code, mimetype="application/json")
            del _cache[key]
        if now < _cooldown_until:
            return _json_error("LRCLIB upstream temporarily rate-limited/unavailable",
                               _cooldown_status,
                               max(1, math.ceil(_cooldown_until - now)))

    try:
        r = requests.get(BASE + path, params=params, allow_redirects=False,
                         headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                         timeout=(3, 10))
    except requests.RequestException as exc:
        LOG.warning("LRCLIB request failed (%s)", type(exc).__name__)
        with _lock:
            _cooldown_until = max(_cooldown_until, time.monotonic() + 900)
            _cooldown_status = 503
        return _json_error("LRCLIB upstream unavailable", 503, 900)

    if r.status_code in (429, 503):
        default = 3600 if r.status_code == 429 else 900
        try:
            delay = int(r.headers.get("Retry-After", default))
        except (ValueError, TypeError):
            delay = default
        delay = min(7200, max(1, delay))
        with _lock:
            _cooldown_until = max(_cooldown_until, time.monotonic() + delay)
            _cooldown_status = r.status_code
        return _json_error("LRCLIB upstream temporarily unavailable", r.status_code, delay)

    if r.status_code not in (200, 400, 404):
        LOG.warning("LRCLIB unexpected status=%s", r.status_code)
        return _json_error("LRCLIB upstream error", 502)

    data = r.content
    if len(data) > MAX_RESPONSE:
        return _json_error("LRCLIB upstream response too large", 502)
    try:
        parsed = r.json()
    except ValueError:
        return _json_error("LRCLIB upstream response is not JSON", 502)
    if r.status_code == 200 and not isinstance(parsed, (list, dict)):
        return _json_error("LRCLIB upstream response has invalid shape", 502)

    if r.status_code in (200, 404):
        ttl = 86400 if r.status_code == 200 else 900
        with _lock:
            _cache[key] = (time.monotonic() + ttl, r.status_code, data)
            _cache.move_to_end(key)
            while len(_cache) > CACHE_LIMIT:
                _cache.popitem(last=False)
    return Response(data, status=r.status_code, mimetype="application/json")


def _params(mode):
    params = dict(request.args)
    if any(len(v) > 256 for v in params.values()):
        return None
    if any(key not in ALLOWED for key in params):
        return None
    if mode == "search":
        if not any(params.get(k) for k in ("q", "track_name")):
            return None
    elif mode == "get":
        if not all(params.get(k) for k in ("track_name", "artist_name", "album_name", "duration")):
            return None
        try:
            duration = float(params["duration"])
            if not (0 < duration < 86400 and math.isfinite(duration)):
                return None
        except ValueError:
            return None
    return params


@app.route("/api/get", methods=["GET"], endpoint="lrclib_get")
@app.route("/api/get-cached", methods=["GET"], endpoint="lrclib_get_cached")
@require_auth_decorator(permission="r")
def get_lyrics():
    mode = _params("get")
    if mode is None:
        return _json_error("Invalid LRCLIB track metadata", 400)
    return _request(request.path, mode)


@app.route("/api/search", methods=["GET"], endpoint="lrclib_search")
@require_auth_decorator(permission="r")
def search_lyrics():
    mode = _params("search")
    if mode is None:
        return _json_error("Invalid LRCLIB search query", 400)
    return _request("/api/search", mode)


@app.route("/api/get/<int:track_id>", methods=["GET"], endpoint="lrclib_get_by_id")
@require_auth_decorator(permission="r")
def get_by_id(track_id):
    if track_id <= 0 or request.args:
        return _json_error("Invalid LRCLIB track ID", 400)
    return _request(f"/api/get/{track_id}", {})
