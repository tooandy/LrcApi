"""Bounded source aggregation with conservative metadata matching and quality scoring."""
from concurrent.futures import ThreadPoolExecutor, wait
import logging
import os
import re
import threading
import time

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


def _quality(item, title, artist, album, duration):
    if not isinstance(item, dict) or not isinstance(item.get("lyrics"), str):
        return None
    text = item["lyrics"]
    if not text.strip() or len(text) > 200000:
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
            if abs(float(duration) - float(item["duration"])) > 2:
                return None
        except (TypeError, ValueError, OverflowError):
            return None
    synced = bool(re.search(r"\[\d{1,3}:\d{2}(?:[.:]\d+)?\]", text))
    score = round(.53 * t + .35 * a + .12 * al + (.08 if synced else 0), 4)
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
