"""Conservative anonymous QQ Music lyric/artwork source.

Proven working API contract (2026-11-14):
  POST https://u.y.qq.com/cgi-bin/musicu.fcg
  GET  https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg
  GET  https://y.gtimg.cn/music/photo_new/T00{1,2}R300x300M000{mid}.jpg

No login cookie, protected playback URL, file write, or user credentials.
Search engines can return different live recordings with an identical title,
so title/artist/album/duration are checked BEFORE fetching lyrics.
"""
from __future__ import annotations

import base64
import binascii
import logging
import math
import re
import time

import requests

from .lrclib import similarity

LOG = logging.getLogger(__name__)
_SEARCH_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
_ART_URL = "https://y.gtimg.cn/music/photo_new/"
_HEADERS = {
    "User-Agent": "HomeLrcAPI/1.0 (private lyric search)",
    "Referer": "https://y.qq.com/",
}
_MID = re.compile(r"[a-zA-Z0-9]{14}\Z")
# Safe under-approximation: never silently accept a concert album in place of
# a studio recording when the client supplied no album/duration context.
_LIVE_ALBUM = re.compile(r"演唱会|演唱會|现场|現場|\blive\b|\bconcert\b", re.I)
_MAX_RESPONSE = 450_000
_DEADLINE = 5.0


def cover_url(mid: str, kind: str = "album", size: int = 300) -> str:
    """Construct a pinned, HTTPS-only QQ cover URL; never trust a provided URL."""
    if kind not in ("album", "artist") or size not in (150, 300, 500, 800):
        return ""
    if not isinstance(mid, str) or not _MID.fullmatch(mid):
        return ""
    marker = "T002" if kind == "album" else "T001"
    return f"{_ART_URL}{marker}R{size}x{size}M000{mid}.jpg"


def _search(query: str, search_type: int = 0) -> list[dict]:
    if not isinstance(query, str) or not query.strip() or len(query) > 260:
        return []
    if search_type not in (0, 1, 2):
        return []
    payload = {
        "music.search.SearchCgiService": {
            "module": "music.search.SearchCgiService",
            "method": "DoSearchForQQMusicDesktop",
            "param": {
                "query": query,
                "search_type": search_type,
                "page_num": 1,
                "num_per_page": 8,
            },
        },
    }
    try:
        response = requests.post(_SEARCH_URL, json=payload, headers=_HEADERS,
                                 timeout=(1.7, 2.5), allow_redirects=False)
        if response.status_code != 200 or len(response.content) > _MAX_RESPONSE:
            return []
        data = response.json()
        outer = data.get("music.search.SearchCgiService") or {}
        if data.get("code") != 0 or outer.get("code") != 0:
            return []
        body = ((outer.get("data") or {}).get("body") or {})
        category = {0: "song", 1: "singer", 2: "album"}[search_type]
        records = ((body.get(category) or {}).get("list") or [])
        return [record for record in records[:8] if isinstance(record, dict)]
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        LOG.info("QQ Music search unavailable: %s", type(exc).__name__)
        return []


def _metadata(song: dict) -> dict | None:
    title = song.get("name")
    mid = song.get("mid")
    album = song.get("album") or {}
    performers = song.get("singer") or []
    if (not isinstance(title, str) or not isinstance(mid, str) or
            not _MID.fullmatch(mid) or not isinstance(album, dict) or
            not isinstance(performers, list)):
        return None
    names = [name for s in performers[:5] if isinstance(s, dict)
             if isinstance((name := s.get("name")), str) and name.strip()]
    if not names:
        return None
    album_name = album.get("name") if isinstance(album.get("name"), str) else ""
    seconds = song.get("interval")
    if isinstance(seconds, bool):
        seconds = None
    if seconds is not None:
        try:
            seconds = float(seconds)
            if not math.isfinite(seconds) or seconds <= 0 or seconds > 7200:
                seconds = None
        except (ValueError, TypeError, OverflowError):
            seconds = None
    return {
        "id": mid, "title": title, "artist": " ".join(names),
        "album": album_name, "duration": seconds,
        "cover": cover_url(album.get("mid"), "album"),
    }


def _exact_song_candidates(title: str, artist: str, album: str = "",
                           duration=None) -> list[dict]:
    if not title or not artist:
        return []
    query = f"{artist} {title}"
    records = _search(query, 0)
    kept = []
    for song in records:
        candidate = _metadata(song)
        if candidate is None:
            continue
        if similarity(title, candidate["title"]) < .92:
            continue
        if similarity(artist, candidate["artist"]) < .86:
            continue
        if album:
            if similarity(album, candidate["album"]) < .78:
                continue
        elif _LIVE_ALBUM.search(candidate["album"]) and not _LIVE_ALBUM.search(title):
            continue
        if duration not in (None, "") and candidate["duration"] is not None:
            try:
                expected = float(duration)
                if not math.isfinite(expected) or abs(expected - candidate["duration"]) > 2:
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
        kept.append(candidate)
        if len(kept) >= 3:
            break
    return kept


def _lyric(mid: str, budget: float = 3.0) -> str:
    if not isinstance(mid, str) or not _MID.fullmatch(mid) or budget < .5:
        return ""
    try:
        response = requests.get(
            _LYRIC_URL,
            params={"songmid": mid, "format": "json", "outCharset": "utf-8"},
            headers=_HEADERS, timeout=(min(1.3, budget / 2), min(2.5, budget)),
            allow_redirects=False,
        )
        if response.status_code != 200 or len(response.content) > _MAX_RESPONSE:
            return ""
        data = response.json()
        if data.get("code") != 0:
            return ""
        encoded = data.get("lyric")
        if not isinstance(encoded, str) or not encoded or len(encoded) > 280_000:
            return ""
        plain = base64.b64decode(encoded, validate=True).decode("utf-8")
        if len(plain) > 200_000 or not plain.strip():
            return ""
        # Plain lyrics may exist; synchronized lyrics are preferred, not forged.
        return plain
    except (requests.RequestException, ValueError, TypeError,
            AttributeError, UnicodeError, binascii.Error) as exc:
        LOG.info("QQ Music lyric unavailable: %s", type(exc).__name__)
        return ""


def search(title: str, artist: str = "", album: str = "") -> list[dict]:
    """Return up to two safely matched QQ lyric candidates, without writes."""
    if not title or not artist:
        return []
    start = time.monotonic()
    candidates = _exact_song_candidates(title, artist, album)
    results = []
    for item in candidates:
        budget = _DEADLINE - (time.monotonic() - start)
        if budget < .5 or len(results) >= 2:
            break
        lyric = _lyric(item["id"], budget=budget)
        if not lyric:
            continue
        results.append(dict(item, lyrics=lyric, source="qqmusic",
                            verified=False))
    return results


def cover_mid(*, title: str = "", artist: str = "", album: str = "",
              kind: str = "album") -> str:
    """Find a high-confidence album or singer image MID; no open redirects."""
    if kind == "artist":
        if not artist:
            return ""
        for row in _search(artist, 1):
            name, mid = row.get("singerName"), row.get("singerMID")
            if (isinstance(name, str) and isinstance(mid, str) and
                    similarity(artist, name) >= .95 and _MID.fullmatch(mid)):
                return mid
        return ""
    if kind not in ("album", "music"):
        return ""
    # Song-side album MID has already been cross-checked with requested song
    # and performer, so it is the safest way to identify album artwork.
    if title and artist:
        for song in _exact_song_candidates(title, artist, album):
            raw_album = song.get("cover", "")
            match = re.search(r"T002R300x300M000([A-Za-z0-9]{14})\.jpg\Z", raw_album)
            if match:
                return match.group(1)
    if album:
        for row in _search(album, 2):
            name, mid, singer = (row.get("albumName"),
                                 row.get("albumMID"), row.get("singerName"))
            if (not isinstance(name, str) or not isinstance(mid, str) or
                    not _MID.fullmatch(mid) or similarity(album, name) < .95):
                continue
            if artist and (not isinstance(singer, str)
                           or similarity(artist, singer) < .85):
                continue
            return mid
    return ""
