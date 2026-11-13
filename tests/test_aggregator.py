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


def test_exact_synced_quality_never_exceeds_one_hundred_percent():
    base = {
        "title": "Chasing Pavements", "artist": "Adele",
        "album": "19", "lyrics": "[00:04.660]I've made up my mind",
        "source": "netease",
    }
    full = searchx._quality(base, "Chasing Pavements", "Adele", "19", 210)
    assert full is not None
    assert full["score"] == 1.0
    assert full["synced"] is True
    assert full["verified"] is False

    unsynced = searchx._quality(dict(base, lyrics="I've made up my mind"),
                                "Chasing Pavements", "Adele", "19", 210)
    assert unsynced is not None
    assert unsynced["score"] == .92
    assert unsynced["synced"] is False
    assert full["score"] > unsynced["score"]

    no_album_query = searchx._quality(base, "Chasing Pavements", "Adele", "", None)
    assert no_album_query is not None
    assert no_album_query["score"] == 1.0


def test_live_acoustic_remix_instrumental_versions_are_not_silent_substitutes():
    base = {"title": "Chasing Pavements", "artist": "Adele",
            "album": "19", "lyrics": "[00:04.660]Lyric"}
    for different in (
        "Chasing Pavements (Live at Hotel Cafe)",
        "Chasing Pavements - Acoustic",
        "Chasing Pavements (Remix)",
        "Chasing Pavements (Instrumental)",
        "Chasing Pavements (Remastered 2015)",
        "Chasing Pavements (Demo)",
    ):
        alternative = dict(base, title=different)
        assert searchx._quality(alternative, "Chasing Pavements", "Adele", "19", None) is None
        assert searchx._quality(base, different, "Adele", "19", None) is None

    # Matching the same explicit edition remains valid.
    live = dict(base, title="Chasing Pavements (Live)")
    assert searchx._quality(live, "Chasing Pavements (Live)", "Adele", "19", None) is not None
    # Words such as "Live" are not necessarily edition markers.
    assert searchx._recording_markers("I Want to Live") == set()
    assert searchx._recording_markers("Live Forever") == set()
    assert searchx._quality(dict(base, title="I Want to Live"),
                            "I Want to Live", "Adele", "19", None)["score"] == 1


def test_aggregator_filters_wrong_recordings_and_ranks_exact_synced_first():
    searchx._CACHE.clear()
    query = {"title": "Chasing Pavements", "artist": "Adele", "album": "19"}
    exact_synced = dict(query, lyrics="[00:03.00]Exact studio", source="kugou")
    bad_live = dict(query, title="Chasing Pavements (Live at Hotel Cafe)",
                    lyrics="[00:03.00]Different take", source="netease")
    plain = dict(query, lyrics="Unsynced studio lyrics", source="netease")
    with patch.object(searchx.kugou, "search", return_value=[bad_live, exact_synced]), \
         patch.object(searchx.netease, "search", return_value=[plain]), \
         patch.object(searchx.lrclib, "search", return_value=[]) as lrclib_search:
        ranked = searchx.search_all(**query, timeout=.5)
        assert len(ranked) == 2
        assert ranked[0]["source"] == "kugou"
        assert ranked[0]["score"] == 1.0
        assert ranked[1]["score"] == .92
        assert all(0 <= row["score"] <= 1 for row in ranked)
        assert all(row["verified"] is False for row in ranked)
        lrclib_search.assert_not_called()


def test_scoring_does_not_accept_wrong_artist_or_wrong_duration():
    base = {"title": "Chasing Pavements", "artist": "Adele",
            "album": "19", "duration": 210, "lyrics": "[00:03.00]Studio"}
    assert searchx._quality(dict(base, artist="Another Singer"),
                            "Chasing Pavements", "Adele", "19", 210) is None
    assert searchx._quality(dict(base, duration=225),
                            "Chasing Pavements", "Adele", "19", 210) is None



def test_provider_malformed_metadata_and_non_finite_duration_are_rejected():
    base = {"title": "Track", "artist": "Artist", "album": "Album",
            "lyrics": "[00:01.00]Words"}
    for key in ("title", "artist", "album"):
        assert searchx._quality(dict(base, **{key: 123}),
                                "Track", "Artist", "Album", 210) is None
    for bad_duration in ("NaN", "Infinity", "-Infinity"):
        assert searchx._quality(dict(base, duration=bad_duration),
                                "Track", "Artist", "Album", 210) is None
    assert searchx._quality(dict(base, duration=210),
                            "Track", "Artist", "Album", "NaN") is None



def test_legacy_providers_preserve_real_source_identity_for_comparison():
    searchx._CACHE.clear()
    song = {"title": "Chasing Pavements", "artist": "Adele",
            "album": "19", "lyrics": "[00:02.000]Words"}
    with patch.object(searchx.kugou, "search", return_value=[dict(song)]), \
         patch.object(searchx.netease, "search",
                      return_value=[dict(song, lyrics="[00:03.000]Other timing")]), \
         patch.object(searchx.lrclib, "search", return_value=[]):
        matches = searchx.search_all("Chasing Pavements", "Adele", "19", timeout=.5)
    assert len(matches) == 2
    assert {row["source"] for row in matches} == {"kugou", "netease"}
    assert {row["lyrics"] for row in matches} == {
        "[00:02.000]Words", "[00:03.000]Other timing",
    }
    assert all(row["score"] == 1.0 for row in matches)
