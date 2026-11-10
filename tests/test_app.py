def test_readonly_liveness(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/time").status_code == 200
    assert client.get("/src").json["mode"] == "read-only"
    assert client.get("/readyz").status_code == 200


def test_auth_and_removed_write_surface(client):
    assert client.get("/lyrics?title=Hello").status_code == 401
    assert client.get("/api/search?track_name=Hello").status_code == 401
    assert client.get("/login").status_code == 404
    assert client.get("/file/music.flac").status_code == 404
    assert client.get("/api/v1/file/list", headers={"Authorization":"offline-test-key"}).status_code == 404
    assert client.post("/tag", json={}, headers={"Authorization":"offline-test-key"}).status_code == 403
    assert client.post("/login-api", json={}).status_code == 403
    assert client.post("/api/v1/db", json={}, headers={"Authorization":"offline-test-key"}).status_code == 403


def test_unknown_token_is_rejected(client):
    assert client.get("/lyrics?title=X", headers={"Authorization": "wrong"}).status_code == 401
    assert client.get("/lyrics?title=X", headers={"Authentication": "wrong"}).status_code == 401


def test_missing_auth_configuration_fails_closed(client, monkeypatch):
    monkeypatch.delenv("API_AUTH")
    assert client.get("/lyrics?title=Hello",headers={"Authorization":"offline-test-key"}).status_code == 401
    assert client.get("/readyz").status_code == 503
