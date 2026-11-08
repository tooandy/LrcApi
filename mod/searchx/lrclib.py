"""Conservative LRCLIB fallback for legacy /lyrics and /jsonapi.

Only returns synced text when title/artist match confidently.
No writes, no retries, one upstream request per cache miss.
"""
import difflib
import logging
import re
import threading
import time
import unicodedata

import requests

LOGGER = logging.getLogger(__name__)
HEADERS = {"User-Agent": "HomeServer-LrcApi/0.1 (https://github.com/tooandy/LrcApi)"}
_lock = threading.Lock()
_cooldown_until = 0
_cache = {}
_CACHE_CAP = 256


def _norm(text):
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", text or "").casefold())


def _score(expected, candidate):
    if not expected:
        return 1.0
    lhs, rhs = _norm(expected), _norm(candidate)
    if not lhs or not rhs:
        return 0.0
    if lhs == rhs:
        return 1.0
    return difflib.SequenceMatcher(None, lhs, rhs).ratio()


def search(title, artist, album="", duration=None):
    global _cooldown_until
    if not title or not artist or len(title) > 200 or len(artist) > 200:
        return []
    key = (title, artist, album, duration)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        if now < _cooldown_until:
            return []

    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    try:
        response = requests.get(
            "https://lrclib.net/api/search",
            params=params, headers=HEADERS, timeout=(3, 9),
            allow_redirects=False,
        )
        if response.status_code in (429, 503):
            try:
                wait = int(response.headers.get("Retry-After", 3600 if response.status_code == 429 else 900))
            except (TypeError, ValueError):
                wait = 3600 if response.status_code == 429 else 900
            with _lock:
                _cooldown_until = max(_cooldown_until, time.monotonic() + min(7200, max(60, wait)))
            return []
        if response.status_code != 200:
            return []
        records = response.json()
        if not isinstance(records, list):
            return []
    except (requests.RequestException, ValueError) as exc:
        LOGGER.info("LRCLIB legacy fallback unavailable: %s", type(exc).__name__)
        with _lock:
            _cooldown_until = max(_cooldown_until, time.monotonic() + 900)
        return []

    matches = []
    for item in records[:20]:
        if not isinstance(item, dict):
            continue
        synced = item.get("syncedLyrics")
        if not isinstance(synced, str) or not re.search(r"\[\d{1,3}:\d{2}[.:]\d", synced):
            continue
        track_score = _score(title, item.get("trackName") or item.get("name"))
        artist_score = _score(artist, item.get("artistName"))
        album_score = _score(album, item.get("albumName"))
        if track_score < .92 or artist_score < .85 or (album and album_score < .8):
            continue
        if duration is not None:
            try:
                if abs(float(duration) - float(item.get("duration"))) > 3:
                    continue
            except (ValueError, TypeError):
                continue
        matches.append((track_score + artist_score + album_score, {
            "title": item.get("trackName"), "artist": item.get("artistName"),
            "album": item.get("albumName"), "lyrics": synced, "source": "lrclib",
        }))
    matches.sort(reverse=True, key=lambda pair: pair[0])
    result = [item for _score_value, item in matches[:3]]
    with _lock:
        if len(_cache) >= _CACHE_CAP:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic() + (86400 if result else 1800), result)
    return result
