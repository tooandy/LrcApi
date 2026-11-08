from concurrent import futures
import logging
import os

from mod.searchx import kugou, netease, lrclib

LOGGER = logging.getLogger(__name__)


def search_all(title, artist, album, timeout=15, duration=None):
    """Preserve legacy providers; use LRCLIB only when both produce no lyrics."""
    sources = (kugou, netease)
    results = []
    with futures.ThreadPoolExecutor(max_workers=2) as executor:
        tasks = [executor.submit(source.search, title, artist, album) for source in sources]
        completed, pending = futures.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        for task in tasks:
            if not task.done() or task.cancelled():
                continue
            try:
                value = task.result()
                if isinstance(value, list):
                    results.extend(item for item in value if isinstance(item, dict) and item.get("lyrics"))
            except Exception as exc:
                LOGGER.warning("Legacy lyric source failed: %s", type(exc).__name__)
    if not results and os.environ.get("LRCAPI_LRCLIB_FALLBACK", "1") == "1":
        return lrclib.search(title=title, artist=artist, album=album, duration=duration)
    return results
