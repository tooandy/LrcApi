"""No QQ network calls in CI: recorded structural contracts and forged-edge data."""
import base64
from unittest.mock import Mock, patch

from mod import searchx
from mod.searchx import qqmusic


def song_record(title="晴天", album="叶惠美", duration=269,
                album_mid="000MkMni19ClKG"):
    return {
        "name": title,
        "mid": "0039MnYb0qxYhV",
        "interval": duration,
        "singer": [{"name": "周杰伦", "mid": "0025NhlN2yWrP4"}],
        "album": {"name": album, "mid": album_mid},
    }


def response_json(data, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = data
    result.content = b"{}"
    return result


def fake_search_record(record=None, search_type=0):
    group = {0: "song", 1: "singer", 2: "album"}[search_type]
    return {
        "code": 0,
        "music.search.SearchCgiService": {
            "code": 0, "data": {"body": {group: {"list": [record or song_record()]}}}
        },
    }


def test_qq_api_uses_modern_search_envelope_and_validates_metadata():
    with patch.object(qqmusic.requests, "post",
                      return_value=response_json(fake_search_record())) as request:
        found = qqmusic._exact_song_candidates("晴天", "周杰伦", "叶惠美", 269)
    assert len(found) == 1
    assert found[0]["id"] == "0039MnYb0qxYhV"
    assert found[0]["cover"].startswith("https://y.gtimg.cn/")
    req_url = request.call_args.args[0]
    data = request.call_args.kwargs["json"]
    assert req_url == "https://u.y.qq.com/cgi-bin/musicu.fcg"
    assert "music.search.SearchCgiService" in data
    assert data["music.search.SearchCgiService"]["param"]["search_type"] == 0
    assert request.call_args.kwargs["allow_redirects"] is False


def test_qq_search_rejects_wrong_recording_and_album_before_lyrics():
    candidate = song_record(album="周杰伦地表最强世界巡回演唱会", duration=249)
    candidate["mid"] = "004Fs2FP1EvZYc"
    with patch.object(qqmusic, "_search", return_value=[candidate]):
        assert qqmusic._exact_song_candidates("晴天", "周杰伦", "叶惠美") == []
        assert qqmusic._exact_song_candidates("晴天", "周杰伦") == []
        assert qqmusic._exact_song_candidates("晴天", "周杰伦",
                                               duration=269) == []
        assert qqmusic.cover_mid(title="晴天", artist="周杰伦", album="叶惠美") == ""
    assert qqmusic.cover_url("../../etc/passwd") == ""
    assert qqmusic.cover_url("000MkMni19ClKG", kind="invalid") == ""


def test_qq_search_decodes_real_lyric_contract_without_adding_timestamps():
    data = fake_search_record()
    lyric = "[ti:晴天]\n[ar:周杰伦]\n[00:30.00]故事的小黄花"
    encoded = base64.b64encode(lyric.encode()).decode()
    with patch.object(qqmusic.requests, "post", return_value=response_json(data)), \
         patch.object(qqmusic.requests, "get",
                      return_value=response_json({"code": 0, "lyric": encoded})) as request:
        found = qqmusic.search("晴天", "周杰伦", "叶惠美")
    assert len(found) == 1
    assert found[0]["lyrics"] == lyric
    assert found[0]["source"] == "qqmusic"
    assert found[0]["verified"] is False
    assert found[0]["duration"] == 269
    assert request.call_args.kwargs["params"]["songmid"] == "0039MnYb0qxYhV"
    assert request.call_args.kwargs["allow_redirects"] is False

    with patch.object(qqmusic.requests, "get",
                      return_value=response_json({"code": 0, "lyric": "%%%BAD"})):
        assert qqmusic._lyric("0039MnYb0qxYhV") == ""


def test_qq_album_and_artist_images_require_matched_mids():
    singer = {"singerName": "周杰伦", "singerMID": "0025NhlN2yWrP4"}
    album = {"albumName": "叶惠美", "albumMID": "000MkMni19ClKG",
             "singerName": "周杰伦"}
    with patch.object(qqmusic, "_search", side_effect=[[singer], [album]]):
        assert qqmusic.cover_mid(artist="周杰伦", kind="artist") == "0025NhlN2yWrP4"
        assert qqmusic.cover_mid(artist="周杰伦", album="叶惠美") == "000MkMni19ClKG"
    assert qqmusic.cover_url("0025NhlN2yWrP4", "artist") == (
        "https://y.gtimg.cn/music/photo_new/T001R300x300M0000025NhlN2yWrP4.jpg"
    )
    with patch.object(qqmusic, "_search", return_value=[album]):
        assert qqmusic.cover_mid(artist="其他歌手", album="叶惠美") == ""


def test_aggregator_qq_is_independent_opt_in_and_tie_prefers_old_sources(monkeypatch):
    searchx._CACHE.clear()
    artist, title, album = "周杰伦", "晴天", "叶惠美"
    match = {"title": title, "artist": artist, "album": album,
             "lyrics": "[00:03.00]same", "duration": 269}
    with patch.object(searchx.kugou, "search", return_value=[dict(match)]), \
         patch.object(searchx.netease, "search", return_value=[]), \
         patch.object(searchx.qqmusic, "search",
                      return_value=[dict(match, lyrics="[00:03.50]QQ")]) as qq, \
         patch.object(searchx.lrclib, "search", return_value=[]):
        # Test fixture disables QQ by default; no changed behavior when disabled.
        first = searchx.search_all(title, artist, album, duration=269)
        assert len(first) == 1
        qq.assert_not_called()
        searchx._CACHE.clear()
        monkeypatch.setenv("LRCAPI_QQMUSIC_ENABLED", "1")
        results = searchx.search_all(title, artist, album, duration=269)
        assert len(results) == 2
        assert results[0]["source"] == "kugou"
        assert results[1]["source"] == "qqmusic"
        assert all(0 <= x["score"] <= 1 and x["verified"] is False for x in results)


def test_cover_endpoint_auth_and_fixed_https_source(client, monkeypatch):
    monkeypatch.setenv("LRCAPI_QQMUSIC_ENABLED", "1")
    assert client.get("/cover?artist=周杰伦&album=叶惠美").status_code == 401
    token = {"Authorization": "offline-test-key"}
    photo = Mock()
    photo.status_code = 200
    photo.headers = {"Content-Type": "image/jpeg", "Content-Length": "8"}
    photo.iter_content.return_value = [b"\xff\xd8\xff\xdbTEST"]
    photo.close.return_value = None
    with patch.object(qqmusic, "cover_mid", return_value="000MkMni19ClKG"), \
         patch("api.qq_cover.requests.get", return_value=photo) as get:
        r = client.get("/cover?artist=周杰伦&album=叶惠美", headers=token)
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.data == b"\xff\xd8\xff\xdbTEST"
    assert get.call_args.args[0] == (
        "https://y.gtimg.cn/music/photo_new/T002R300x300M000000MkMni19ClKG.jpg"
    )
    assert get.call_args.kwargs["allow_redirects"] is False
    assert photo.close.called


def test_cover_legacy_routes_never_fetch_arbitrary_urls(client, monkeypatch):
    monkeypatch.setenv("LRCAPI_QQMUSIC_ENABLED", "1")
    auth = {"Authorization": "offline-test-key"}
    assert client.get("/cover", headers=auth).status_code == 400
    assert client.get("/api/v1/cover/evil?artist=周杰伦", headers=auth).status_code == 404
    assert client.get("/cover?album=" + "x" * 300, headers=auth).status_code == 400

    with patch.object(qqmusic, "cover_mid", return_value="000MkMni19ClKG"), \
         patch("api.qq_cover.requests.get") as get:
        bad = Mock()
        bad.status_code = 302
        bad.headers = {"Content-Type": "image/jpeg"}
        bad.close.return_value = None
        get.return_value = bad
        assert client.get("/api/v1/cover/album?artist=周杰伦&album=叶惠美",
                          headers=auth).status_code == 404
        assert get.call_args.args[0].startswith("https://y.gtimg.cn/")

    with patch("api.qq_cover.requests.get") as get:
        with patch.object(qqmusic, "cover_mid", return_value=""):
            assert client.get("/cover?album=NoMatch&artist=NoMatch",
                              headers=auth).status_code == 404
        get.assert_not_called()


def test_cover_rejects_oversized_or_non_jpeg_media(client, monkeypatch):
    monkeypatch.setenv("LRCAPI_QQMUSIC_ENABLED", "1")
    auth = {"Authorization": "offline-test-key"}
    with patch.object(qqmusic, "cover_mid", return_value="000MkMni19ClKG"):
        with patch("api.qq_cover.requests.get") as get:
            photo = Mock()
            photo.status_code = 200
            photo.headers = {"Content-Type": "image/jpeg",
                             "Content-Length": "3000000"}
            photo.close.return_value = None
            get.return_value = photo
            assert client.get("/cover?album=叶惠美&artist=周杰伦",
                              headers=auth).status_code == 502
            photo.headers = {"Content-Type": "text/html"}
            assert client.get("/cover?album=叶惠美&artist=周杰伦",
                              headers=auth).status_code == 502
