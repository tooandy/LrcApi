import json
from unittest.mock import patch

from mod.searchx.lrclib import CLIENT


def _auth():
    return {"Authorization": "offline-test-key"}


def test_v1_get_optional_params_and_invalid_duration(client):
    with patch.object(CLIENT, "get", return_value=(200,b'{"id":123}',{})) as call:
        r = client.get("/api/get?track_name=Title&artist_name=Artist",headers=_auth())
    assert r.status_code == 200
    assert r.json["id"] == 123
    assert call.call_args.args[:2] == ("/api/get", {"track_name":"Title","artist_name":"Artist"})
    r = client.get("/api/get?track_name=Title&artist_name=Artist&duration=5000",headers=_auth())
    assert r.status_code == 400


def test_empty_search_is_array(client):
    with patch.object(CLIENT, "get") as remote:
        result=client.get("/api/search",headers=_auth())
        beta=client.get("/api/v2/search",headers=_auth())
    assert result.status_code == 200 and result.json == []
    assert beta.status_code == 200 and beta.json == []
    remote.assert_not_called()


def test_get_cached_only_queries_local_cache(client):
    with patch.object(CLIENT, "get", return_value=(404,b'{"message":"Not cached"}',{})) as remote:
        result=client.get("/api/get-cached?track_name=Song&artist_name=Artist",headers=_auth())
    assert result.status_code == 404
    assert remote.call_args.kwargs["only_cached"] is True


def test_beta_search_and_get_by_id(client):
    with patch.object(CLIENT, "get", return_value=(200,b'[]',{})) as remote:
        assert client.get("/api/v2/search?q=Music",headers=_auth()).status_code == 200
        assert remote.call_args.args[0] == "/api/v2/search"
        assert client.get("/api/get/123",headers=_auth()).status_code == 200
        assert remote.call_args.args[0] == "/api/get/123"


def test_protected_read_endpoints(client):
    assert client.get("/api/search?track_name=X").status_code == 401
    assert client.get("/api/get?track_name=X&artist_name=Y").status_code == 401
