import time
from unittest.mock import patch

from mod import searchx


def test_bounded_legacy_threads_do_not_block_request():
    searchx._CACHE.clear()
    def slow(*args):
        time.sleep(.45)
        return []
    with patch.object(searchx.kugou,"search",side_effect=slow), \
         patch.object(searchx.netease,"search",side_effect=slow), \
         patch.object(searchx.lrclib,"search",return_value=[]):
        start = time.monotonic()
        assert searchx.search_all("Unique Test Song","Singer",timeout=.04)==[]
        elapsed = time.monotonic()-start
    assert elapsed < .30


def test_untrusted_weak_match_not_accepted():
    searchx._CACHE.clear()
    weak = {"title":"Completely Different Song","artist":"Singer",
            "album":"Album","lyrics":"[00:01.00]wrong"}
    with patch.object(searchx.kugou,"search",return_value=[weak]), \
         patch.object(searchx.netease,"search",return_value=[]), \
         patch.object(searchx.lrclib,"search",return_value=[]):
        assert searchx.search_all("My Song","Singer",album="Album",timeout=.1)==[]
