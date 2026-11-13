from pathlib import Path
from unittest.mock import patch

from api.lyrics import local_music_path
from mod import confirmed_store


def test_path_mapping_and_traversal(monkeypatch, tmp_path):
    root = tmp_path / "music"
    root.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME_SERVER_MUSIC_ROOT", str(root))
    assert local_music_path("/music/Artist/track.flac") == str(root / "Artist" / "track.flac")
    assert local_music_path("/mnt/v2/Music/Artist/track.flac") == str(root / "Artist" / "track.flac")
    assert local_music_path("/music/../etc/passwd") == ""
    assert local_music_path("/etc/passwd") == ""


def test_local_sidecar_is_unverified(client, tmp_path):
    root = tmp_path / "music"
    audio = root / "song.flac"
    audio.write_bytes(b"fake audio")
    audio.with_suffix(".lrc").write_text("[00:01.00]Unverified lyrics", encoding="utf-8")
    with patch("mod.searchx.search_all") as remote:
        r = client.get("/jsonapi?path=/music/song.flac", headers={"Authorization": "offline-test-key"})
    assert r.status_code == 200
    assert r.json[0]["verified"] is False
    assert r.json[0]["source"] == "sidecar_default"
    remote.assert_not_called()


def test_sqlite_confirmed_overrides_sidecar(client, tmp_path):
    root = tmp_path / "music"
    audio = root / "song.flac"
    audio.write_bytes(b"fake audio")
    audio.with_suffix(".lrc").write_text("[00:01.00]Wrong sidecar", encoding="utf-8")
    confirmed_store.confirm(path="/music/song.flac", lyrics="[00:01.00]Confirmed lyrics")
    r = client.get("/jsonapi?path=/music/song.flac", headers={"Authorization": "offline-test-key"})
    assert r.status_code == 200
    assert r.json[0]["verified"] is True
    assert "Confirmed lyrics" in r.json[0]["lyrics"]
    assert "Wrong sidecar" not in r.json[0]["lyrics"]


def test_negative_result_not_cached_for_day(client):
    with patch("mod.searchx.search_all",
               side_effect=[[],[{"title":"Test","artist":"Singer",
                                "lyrics":"[00:01.00]New lyrics","verified":False}]]) as lookup:
        r1 = client.get("/lyrics?title=Test&artist=Singer",
                        headers={"Authorization": "offline-test-key"})
        r2 = client.get("/lyrics?title=Test&artist=Singer",
                        headers={"Authorization": "offline-test-key"})
    assert r1.status_code == 404
    assert r2.status_code == 200
    assert lookup.call_count == 2


def test_sidecar_symlink_outside_music_is_not_read(client, tmp_path):
    root = tmp_path / "music"
    audio = root / "song.flac"
    audio.write_bytes(b"fake audio")
    secret = tmp_path / "outside-lyrics.txt"
    secret.write_text("[00:01.00]Do not expose")
    audio.with_suffix(".lrc").symlink_to(secret)
    with patch("api.lyrics.tag.read", return_value={}), \
         patch("mod.searchx.search_all", return_value=[]):
        result = client.get("/jsonapi?path=/music/song.flac",
                            headers={"Authorization":"offline-test-key"})
    assert result.status_code == 200
    assert result.json == []


def test_legacy_core_http_routes_use_bounded_match_scores(client):
    """Old /lyrics, /jsonapi and v1 alias responses share the fixed ranker."""
    from mod import searchx

    searchx._CACHE.clear()
    exact = {"title": "Chasing Pavements", "artist": "Adele", "album": "19",
             "lyrics": "[00:04.660]I've made up my mind", "source": "kugou"}
    wrong_take = dict(exact, title="Chasing Pavements (Live)",
                      lyrics="[00:04.660]Wrong performance")
    query = "title=Chasing%20Pavements&artist=Adele&album=19"
    token = {"Authorization": "offline-test-key"}

    with patch.object(searchx.kugou, "search", return_value=[wrong_take, exact]), \
         patch.object(searchx.netease, "search", return_value=[]), \
         patch.object(searchx.lrclib, "search", return_value=[]):
        json_resp = client.get("/jsonapi?" + query, headers=token)
        assert json_resp.status_code == 200
        assert len(json_resp.json) == 1
        assert json_resp.json[0]["score"] == 1.0
        assert json_resp.json[0]["synced"] is True
        assert json_resp.json[0]["verified"] is False

        v1_resp = client.get("/api/v1/lyrics/advance?" + query, headers=token)
        assert v1_resp.status_code == 200
        assert v1_resp.json == json_resp.json

        raw_resp = client.get("/lyrics?" + query, headers=token)
        assert raw_resp.status_code == 200
        assert "I've made up my mind" in raw_resp.get_data(as_text=True)

        v1_raw = client.get("/api/v1/lyrics/single?" + query, headers=token)
        assert v1_raw.status_code == 200
        assert v1_raw.data == raw_resp.data
