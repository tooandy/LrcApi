"""One shared LRCLIB v1/v2 read-only client with rate limit, bounded cache and cooldown.

Both legacy-fallback and LRCLIB-native routes use the same client instance.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import difflib
import json
import logging
import re
import threading
import time
import unicodedata

import requests

LOG = logging.getLogger(__name__)
BASE = "https://lrclib.net"
UA = "HomeServer-LrcApi/2.0 (+https://github.com/tooandy/LrcApi)"
MAX_RESPONSE = 2_000_000


def _retry_after(text: str | None, fallback: int) -> int:
    if not text:
        return fallback
    try:
        seconds = int(text)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(text)
            seconds = int((date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return fallback
    return min(86400, max(1, seconds))


class LRCLIBClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache: OrderedDict[tuple, tuple[float, int, bytes]] = OrderedDict()
        self.limit = 256
        self.cooldown_until = 0.0
        self.next_call = 0.0
        self.last_status = 0
        self.counters = {"upstream_ok": 0, "upstream_error": 0, "cache_hits": 0, "throttled": 0}

    def _cached(self, key):
        result = self.cache.get(key)
        if not result:
            return None
        expiry, status, body = result
        if expiry < time.monotonic():
            self.cache.pop(key, None)
            return None
        self.cache.move_to_end(key)
        self.counters["cache_hits"] += 1
        return (status, body, {})

    def get(self, endpoint: str, params: dict | None = None, *, only_cached=False):
        if endpoint not in ("/api/search", "/api/get", "/api/v2/search") and not re.fullmatch(
                r"/api/get/[1-9]\d{0,12}", endpoint):
            return (400, b'{"message":"Invalid endpoint"}', {})
        params = params or {}
        key = (endpoint, tuple(sorted(params.items())))
        with self.lock:
            cached = self._cached(key)
            if cached:
                return cached
            if only_cached:
                return (404, b'{"message":"Not cached"}', {})
            now = time.monotonic()
            if now < self.cooldown_until:
                self.counters["throttled"] += 1
                remaining = max(1, int(self.cooldown_until - now))
                return (429, b'{"message":"LRCLIB unavailable or rate limited"}',
                        {"Retry-After": str(remaining)})
            # Requests are serialized and separated. Does not initiate bursts.
            delay = self.next_call - now
            if delay > 0:
                time.sleep(min(delay, .6))
            self.next_call = time.monotonic() + .4

            try:
                response = requests.get(BASE + endpoint, params=params, stream=True,
                                        allow_redirects=False, timeout=(3, 9),
                                        headers={"Accept": "application/json", "User-Agent": UA})
                try:
                    status = response.status_code
                    if status in (429, 503):
                        cool = _retry_after(response.headers.get("Retry-After"),
                                            3600 if status == 429 else 900)
                        self.cooldown_until = time.monotonic() + cool
                        self.last_status = status
                        self.counters["throttled"] += 1
                        return (status, b'{"message":"LRCLIB temporarily unavailable"}',
                                {"Retry-After": str(cool)})
                    if status not in (200, 400, 404):
                        self.counters["upstream_error"] += 1
                        return (502, b'{"message":"LRCLIB upstream error"}', {})
                    if int(response.headers.get("Content-Length", "0") or 0) > MAX_RESPONSE:
                        return (502, b'{"message":"Response too large"}', {})
                    parts = []
                    total = 0
                    deadline = time.monotonic() + 11
                    for chunk in response.iter_content(chunk_size=32768):
                        total += len(chunk)
                        if total > MAX_RESPONSE or time.monotonic() > deadline:
                            return (502, b'{"message":"Response exceeded budget"}', {})
                        parts.append(chunk)
                    body = b"".join(parts)
                    value = json.loads(body)
                    if status == 200 and not isinstance(value, (dict, list)):
                        raise ValueError("unexpected JSON type")
                finally:
                    response.close()
            except (requests.RequestException, ValueError) as exc:
                self.cooldown_until = time.monotonic() + 300
                self.last_status = 503
                self.counters["upstream_error"] += 1
                LOG.warning("LRCLIB upstream error type=%s", type(exc).__name__)
                return (503, b'{"message":"LRCLIB unavailable"}', {"Retry-After": "300"})

            self.last_status = status
            if status == 200:
                self.counters["upstream_ok"] += 1
            if status in (200, 404):
                self.cache[key] = (time.monotonic() + (86400 if status == 200 else 300), status, body)
                while len(self.cache) > self.limit:
                    self.cache.popitem(last=False)
            return (status, body, {})


CLIENT = LRCLIBClient()


def _norm(value: str | None) -> str:
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", value or "").casefold())


def similarity(expected, actual):
    left, right = _norm(expected), _norm(actual)
    if not left:
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio() if right else 0


def search(title, artist, album="", duration=None):
    """Return only high-confidence synced lyrics, never a guess from title alone."""
    if not title or not artist:
        return []
    status, payload, _ = CLIENT.get("/api/search", {"track_name": title, "artist_name": artist})
    if status != 200:
        return []
    try:
        records = json.loads(payload)
    except ValueError:
        return []
    if not isinstance(records, list):
        return []
    best = []
    for item in records[:20]:
        if not isinstance(item, dict):
            continue
        synced = item.get("syncedLyrics")
        if not isinstance(synced, str) or not re.search(r"\[\d{1,3}:\d{2}[.:]\d", synced):
            continue
        t = similarity(title, item.get("trackName") or item.get("name"))
        a = similarity(artist, item.get("artistName"))
        al = similarity(album, item.get("albumName"))
        if t < .94 or a < .86 or (album and al < .82):
            continue
        if duration not in ("", None):
            try:
                if abs(float(duration) - float(item.get("duration"))) > 2:
                    continue
            except (ValueError, TypeError, OverflowError):
                continue
        score = round((t * .55 + a * .35 + al * .10), 4)
        best.append((score, {
            "id": item.get("id"), "title": item.get("trackName"),
            "artist": item.get("artistName"), "album": item.get("albumName"),
            "lyrics": synced, "source": "lrclib", "verified": False,
            "score": score, "duration": item.get("duration"),
        }))
    best.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _, item in best[:3]]
