"""Bounded source aggregation with conservative metadata matching and quality scoring."""
from concurrent.futures import ThreadPoolExecutor, wait
import logging
import math
import os
import re
import threading
import time
import unicodedata

from . import kugou, netease, lrclib

LOG = logging.getLogger(__name__)
# Global, bounded pool; per-request executor shutdown never blocks an HTTP worker.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="lyrics-provider")
_SLOTS = threading.BoundedSemaphore(8)
_CACHE_LOCK = threading.Lock()
_CACHE = {}
_MAX_CACHE = 512


def _run(provider, title, artist, album):
    try:
        return provider.search(title, artist, album) or []
    except Exception as exc:
        LOG.warning("Provider %s error: %s", provider.__name__, type(exc).__name__)
        return []
    finally:
        _SLOTS.release()


# Song versions usually share the same name, artist and album. When a result
# explicitly advertises a different recording/mix, it must not be silently
# preferred just because that lyric happens to be synchronized.
_VERSION_MARKERS = {
    "live": r"\blive\b|现场(?:版|录音)?|現場(?:版|錄音)?|演唱会版|演唱會版|ライブ",
    "acoustic": r"\bacoustic\b|不插电|不插電|原声版|原聲版",
    "instrumental": r"\binstrumental\b|伴奏版?|纯音乐版|純音樂版|\bkaraoke\b",
    "remix": r"\bremix\b|混音版",
    "demo": r"\bdemo\b|小样版|小樣版",
    "remaster": r"\bremaster(?:ed)?\b|重制版|重製版",
}


def _recording_markers(value):
    """Read explicit version labels, not words in ordinary song titles.

    'I Want to Live' and 'Live Forever' are song titles, not live editions.
    Only a bracketed or dash-suffixed edition marker is considered reliable.
    """
    title = unicodedata.normalize("NFKC", value or "").casefold()
    contexts = re.findall(r"[\(\[【]([^\)\]】]{1,90})[\)\]】]", title)
    contexts.extend(re.findall(
        r"(?:\s[-–—]\s|\s+version:)[^–—]{1,90}$", title
    ))
    return {name for name, pattern in _VERSION_MARKERS.items()
            if any(re.search(pattern, context, re.I) for context in contexts)}


def _quality(item, title, artist, album, duration):
    if not isinstance(item, dict) or not isinstance(item.get("lyrics"), str):
        return None
    text = item["lyrics"]
    if not text.strip() or len(text) > 200000:
        return None
    # Provider payloads are untrusted; reject malformed metadata before
    # passing them to normalization, which otherwise raises TypeError.
    if any(not isinstance(item.get(field), (str, type(None)))
           for field in ("title", "artist", "album")):
        return None
    if _recording_markers(title) != _recording_markers(item.get("title")):
        return None
    t = lrclib.similarity(title, item.get("title"))
    a = lrclib.similarity(artist, item.get("artist"))
    al = lrclib.similarity(album, item.get("album"))
    if t < (.88 if artist else .97) or (artist and a < .80):
        return None
    if album and al < .68:
        return None
    if duration not in ("", None) and item.get("duration") is not None:
        try:
            expected_duration = float(duration)
            actual_duration = float(item["duration"])
            if not math.isfinite(expected_duration) or not math.isfinite(actual_duration):
                return None
            if abs(expected_duration - actual_duration) > 2:
                return None
        except (TypeError, ValueError, OverflowError):
            return None
    synced = bool(re.search(r"\[\d{1,3}:\d{2}(?:[.:]\d+)?\]", text))
    # The three metadata weights already sum to 1.00. The legacy '+.08'
    # bonus created impossible 108% scores and could rank a wrong *live*
    # recording ahead of a matching studio recording. Give synced lyrics 8%
    # of the final score instead of adding 8% on top of 100%.
    metadata_score = .53 * t + .35 * a + .12 * al
    score = round(min(1.0, max(0.0,
        .92 * metadata_score + (.08 if synced else 0))), 4)
    return dict(item, verified=False, synced=synced, score=score)


def search_all(title, artist="", album="", timeout=7, duration=None):
    if not title or len(title) > 256 or len(artist) > 256 or len(album) > 256:
        return []
    key = (title, artist, album, duration)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] > time.monotonic():
            return [dict(v) for v in hit[1]]

    tasks = []
    for source in (kugou, netease):
        if not _SLOTS.acquire(blocking=False):
            break
        try:
            tasks.append(_EXECUTOR.submit(_run, source, title, artist, album))
        except RuntimeError:
            _SLOTS.release()
    results = []
    if tasks:
        done, _ = wait(tasks, timeout=min(float(timeout), 8))
        for future in done:
            try:
                got = future.result()
                if isinstance(got, list):
                    results.extend(item for item in got if isinstance(item, dict))
            except Exception as exc:
                LOG.warning("Source future failed: %s", type(exc).__name__)

    ranked = []
    for item in results:
        candidate = _quality(item, title, artist, album, duration)
        if candidate:
            ranked.append(candidate)

    # LRCLIB competes as a trustworthy candidate whenever the legacy sources
    # have no high-confidence synced lyrics. Shared source handles its own quotas.
    good_sync = any(x.get("synced") and x["score"] >= .92 for x in ranked)
    if not good_sync and os.environ.get("LRCAPI_LRCLIB_FALLBACK", "1") == "1" and artist:
        for item in lrclib.search(title, artist, album, duration):
            candidate = _quality(item, title, artist, album, duration)
            if candidate:
                ranked.append(candidate)
    ranked.sort(key=lambda item: item["score"], reverse=True)

    # Avoid silently picking poor candidates: strong minimum is required for
    # first-result /lyrics, and all items are explicitly marked unverified.
    ranked = [r for r in ranked if r["score"] >= .89]
    if ranked:
        # Prefer synchronized lyrics among equally credible metadata matches.
        ranked.sort(key=lambda x: (x["score"], x["synced"]), reverse=True)

    with _CACHE_LOCK:
        if len(_CACHE) >= _MAX_CACHE:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (time.monotonic() + (600 if ranked else 60), [dict(x) for x in ranked[:8]])
    return ranked[:8]
