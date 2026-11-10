import json
from unittest.mock import patch, Mock

from mod.searchx.lrclib import CLIENT, LRCLIBClient, search


def test_strict_match_synced_only():
    payload = [
        {"trackName":"Song","artistName":"Singer","albumName":"Album","duration":200,
         "syncedLyrics":"[00:02.00]Valid"},
        {"trackName":"Other","artistName":"Singer","albumName":"Album","duration":200,
         "syncedLyrics":"[00:02.00]Incorrect"},
    ]
    with patch.object(CLIENT,"get",return_value=(200,json.dumps(payload).encode(),{})):
        found=search("Song","Singer","Album",200)
    assert len(found)==1 and found[0]["source"]=="lrclib"
    assert found[0]["verified"] is False


def test_wrong_duration_is_rejected():
    payload = [{"trackName":"Song","artistName":"Singer","duration":100,
                "syncedLyrics":"[00:01.00]OK"}]
    with patch.object(CLIENT, "get",return_value=(200,json.dumps(payload).encode(),{})):
        assert search("Song","Singer",duration=120)==[]


def test_cached_only_and_retry_after_share_state(monkeypatch):
    fake = Mock()
    fake.status_code = 429
    fake.headers = {"Retry-After": "120"}
    with patch("mod.searchx.lrclib.requests.get", return_value=fake) as req:
        target = LRCLIBClient()
        code, body, headers = target.get("/api/search",{"q":"Track"})
        assert code == 429
        assert int(headers["Retry-After"]) == 120
        assert target.get("/api/get",{"track_name":"Track","artist_name":"Singer"})[0] == 429
        assert target.get("/api/search",{"q":"Track"},only_cached=True)[0] == 404
        assert req.call_count == 1
