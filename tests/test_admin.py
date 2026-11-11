"""The editor is a separate loopback app; public Flask app exposes no writes."""
from pathlib import Path
from unittest.mock import patch
import json

import pytest

from admin.server import app, CSRF, _root
from mod import confirmed_store


@pytest.fixture
def editor():
    return app.test_client()


def audio_file(tmp_path, name="Song.flac", content="[00:01.00]Original local"):
    root=tmp_path/"music"
    root.mkdir(exist_ok=True)
    path=root/name
    path.write_bytes(b"not-real-audio")
    path.with_suffix(".lrc").write_text(content,encoding="utf-8")
    return path


def headers(*, csrf=True):
    h={"Origin":"http://localhost:28886"}
    if csrf:
        h["X-CSRF-Token"]=CSRF
    return h


def test_editor_page_and_local_browse(editor, tmp_path):
    audio_file(tmp_path)
    resp=editor.get("/",headers={"Host":"localhost:28886"})
    assert resp.status_code==200
    assert b"index" in resp.data or b"LYRIC STUDIO" in resp.data
    assert b"script src=\"/app.js\"" in resp.data
    assert editor.get("/style.css").status_code==200
    assert editor.get("/app.js").status_code==200
    assert editor.get("/theme.js").status_code==200
    assert b'data-theme="system"' in resp.data
    assert b'id="theme-select"' in resp.data
    assert b'<option value="dark">' in resp.data
    session=editor.get("/api/session")
    assert session.json["mode"]=="loopback"
    assert session.json["writes_music"] is False
    result=editor.get("/api/browse").json
    assert result["items"][0]["name"]=="Song.flac"
    assert result["items"][0]["kind"]=="track"
    assert editor.get("/api/browse?q=no-match").json["items"]==[]


def test_default_local_correct_save_revoke_and_history(editor,tmp_path):
    file=audio_file(tmp_path)
    initial=editor.get("/api/track?path=Song.flac").json
    assert initial["status"]=="sidecar"
    assert "Original local" in initial["active"]
    assert initial["correction_id"] is None
    corrected="[00:01.00]Corrected!\n[00:02.00]Second line"
    r=editor.post("/api/save",json={
        "path":"Song.flac","expected_sha256":initial["active_sha256"],"lyrics":corrected,
    },headers=headers())
    assert r.status_code==200
    assert r.json["writes_music"] is False
    assert r.json["backup_ok"] is True
    backups = list((tmp_path / "private/backups").glob("overrides-*.sqlite3"))
    assert len(backups) == 1 and backups[0].stat().st_size > 0
    updated=editor.get("/api/track?path=Song.flac").json
    assert updated["status"]=="corrected"
    assert updated["active"]==corrected
    assert file.with_suffix(".lrc").read_text()=="[00:01.00]Original local"
    assert file.read_bytes()==b"not-real-audio"
    assert len(editor.get("/api/history?path=Song.flac").json)==1

    revoked=editor.post("/api/revoke",json={
        "path":"Song.flac","expected_sha256":updated["active_sha256"],
    },headers=headers())
    assert revoked.status_code==200
    restored=editor.get("/api/track?path=Song.flac").json
    assert restored["status"]=="sidecar"
    assert restored["active"]==initial["active"]
    records=editor.get("/api/history?path=Song.flac").json
    assert len(records)==1 and records[0]["status"]=="revoked"


def test_admin_rejects_foreign_origin_missing_csrf_and_invalid_host(editor,tmp_path):
    audio_file(tmp_path)
    payload={"path":"Song.flac","expected_sha256":"x","lyrics":"[00:01.00]changed"}
    assert editor.post("/api/save",json=payload).status_code==403
    assert editor.post("/api/save",json=payload,headers=headers(csrf=False)).status_code==403
    assert editor.post("/api/save",json=payload,headers={
        "Origin":"https://evil.example","X-CSRF-Token":CSRF,
    }).status_code==403
    assert editor.get("/api/browse",headers={"Host":"evil.example"}).status_code==403
    assert editor.get("/api/browse",environ_overrides={"REMOTE_ADDR":"192.168.3.4"}).status_code==401
    assert editor.post("/api/save",data="{}",
                       headers={"X-CSRF-Token":CSRF}).status_code==415


def test_no_absolute_paths_and_symlink_escape(editor,tmp_path):
    audio_file(tmp_path)
    assert editor.get("/api/track?path=/etc/passwd").status_code==400
    assert editor.get("/api/track?path=../outside.flac").status_code==400
    assert editor.get("/api/browse?path=../../").status_code==400
    root=tmp_path/"music"
    (root/"escape").symlink_to(tmp_path)
    assert editor.get("/api/browse?path=escape").status_code==400


def test_stale_edit_must_reload(editor,tmp_path):
    audio_file(tmp_path)
    first=editor.get("/api/track?path=Song.flac").json
    r=editor.post("/api/save",json={
      "path":"Song.flac","expected_sha256":first["active_sha256"],"lyrics":"[00:05.00]New"
    },headers=headers())
    assert r.status_code==200
    stale=editor.post("/api/save",json={
      "path":"Song.flac","expected_sha256":first["active_sha256"],"lyrics":"[00:06.00]Stale"
    },headers=headers())
    assert stale.status_code==409


def test_network_candidates_are_never_implicitly_confirmed(editor,tmp_path):
    audio_file(tmp_path)
    response=[{"title":"Song","artist":"Singer","source":"lrclib",
               "lyrics":"[00:01.00]Remote","verified":False,"score":0.98}]
    with patch("admin.server.searchx.search_all",return_value=response) as remote:
        result=editor.get("/api/candidates?path=Song.flac")
    assert result.status_code==200
    assert result.json["items"][0]["verified"] is False
    assert confirmed_store.correction_count()==0
    remote.assert_called_once()


def test_public_api_cannot_reach_editor_or_modify_db(client,tmp_path):
    audio_file(tmp_path)
    assert client.get("/api/track?path=Song.flac").status_code==404
    assert client.get("/admin").status_code==404
    assert client.post("/api/save",json={
        "path":"Song.flac","lyrics":"malicious",
    },headers={"Authorization":"offline-test-key"}).status_code==403
    assert confirmed_store.correction_count()==0


def test_explicit_sidecar_export_and_local_backup_restore(editor, tmp_path):
    """Only the explicit export route changes a .lrc; audio remains untouched."""
    from admin import sidecar_export
    from unittest.mock import patch

    audio=audio_file(tmp_path, content="[00:00.12]Old local")
    backup_dir=tmp_path/"apfs-local-backups"
    backup_dir.mkdir()
    song=editor.get("/api/track?path=Song.flac").json
    corrected="[00:00.150]Corrected and reviewed"
    saved=editor.post("/api/save",json={
        "path":"Song.flac","expected_sha256":song["active_sha256"],"lyrics":corrected
    },headers=headers())
    assert saved.status_code==200
    assert audio.with_suffix(".lrc").read_text()=="[00:00.12]Old local"
    current=editor.get("/api/track?path=Song.flac").json
    with patch.object(sidecar_export,"_require_nfs") as nfs, \
         patch.object(sidecar_export,"_backup_directory",return_value=backup_dir):
        done=editor.post("/api/export",json={
            "path":"Song.flac","expected_sha256":current["active_sha256"]
        },headers=headers())
        assert done.status_code==200, done.json
        assert done.json["writes_music"] is True
        assert audio.with_suffix(".lrc").read_text()==corrected
        assert audio.read_bytes()==b"not-real-audio"
        backups=list(backup_dir.glob("*.lrc"))
        assert len(backups)==1
        assert backups[0].read_text()=="[00:00.12]Old local"
        assert backups[0].stat().st_mode & 0o777==0o600
        after=editor.get("/api/track?path=Song.flac").json
        assert after["sidecar_export"]["status"]=="exported"
        # Prevent false 'undo' while an exported sidecar is still active.
        assert editor.post("/api/revoke",json={
            "path":"Song.flac","expected_sha256":after["active_sha256"]
        },headers=headers()).status_code==409
        rollback=editor.post("/api/restore",json={
            "path":"Song.flac","expected_sha256":after["active_sha256"]
        },headers=headers())
        assert rollback.status_code==200, rollback.json
        assert audio.with_suffix(".lrc").read_text()=="[00:00.12]Old local"
    assert editor.get("/api/track?path=Song.flac").json["sidecar_export"] is None
    current=editor.get("/api/track?path=Song.flac").json
    revoked=editor.post("/api/revoke",json={
        "path":"Song.flac","expected_sha256":current["active_sha256"]
    },headers=headers())
    assert revoked.status_code==200
    assert editor.get("/api/track?path=Song.flac").json["status"]=="sidecar"


def test_export_refuses_outside_nfs_and_changed_sidecar(editor, tmp_path):
    from admin import sidecar_export
    from unittest.mock import patch
    audio=audio_file(tmp_path)
    before=editor.get("/api/track?path=Song.flac").json
    editor.post("/api/save",json={
        "path":"Song.flac","expected_sha256":before["active_sha256"],
        "lyrics":"[00:01.00]Edited"
    },headers=headers())
    current=editor.get("/api/track?path=Song.flac").json
    blocked=editor.post("/api/export",json={
        "path":"Song.flac","expected_sha256":current["active_sha256"]
    },headers=headers())
    assert blocked.status_code==400
    assert audio.with_suffix(".lrc").read_text()=="[00:01.00]Original local"
    with patch.object(sidecar_export,"_require_nfs"),\
         patch.object(sidecar_export,"_backup_directory",return_value=tmp_path):
        exported=editor.post("/api/export",json={
            "path":"Song.flac","expected_sha256":current["active_sha256"]
        },headers=headers())
        assert exported.status_code==200
        audio.with_suffix(".lrc").write_text("[00:02.00]Other program modified this")
        again=editor.post("/api/restore",json={
            "path":"Song.flac","expected_sha256":current["active_sha256"]
        },headers=headers())
        assert again.status_code==400
        assert "Other program" in audio.with_suffix(".lrc").read_text()


def test_lan_requires_password_and_home_subnet(editor, tmp_path):
    import base64
    audio_file(tmp_path)
    lan = {"REMOTE_ADDR": "192.168.3.42", "HTTP_HOST": "192.168.3.7:28886"}
    def basic(user, password):
        token=base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    assert editor.get("/", environ_overrides=lan).status_code == 401
    assert editor.get("/", environ_overrides=lan).headers["WWW-Authenticate"].startswith("Basic")
    assert editor.get("/api/session", environ_overrides=lan).status_code == 401
    assert editor.get("/", environ_overrides=lan,
                      headers=basic("lyrics","wrong")).status_code == 401
    assert editor.get("/", environ_overrides=lan,
                      headers=basic("wrong","offline-test-password-for-lan-2026")).status_code == 401
    granted=editor.get("/api/session", environ_overrides=lan,
                       headers=basic("lyrics","offline-test-password-for-lan-2026"))
    assert granted.status_code == 200
    assert granted.json["mode"] == "lan-authenticated"
    assert granted.json["csrf"]
    assert editor.get("/api/browse", environ_overrides=lan,
                      headers=basic("lyrics","offline-test-password-for-lan-2026")).status_code == 200
    for peer in ("192.168.4.21", "10.0.0.1", "100.100.0.1", "203.0.113.99"):
        network = {"REMOTE_ADDR": peer, "HTTP_HOST": "192.168.3.7:28886"}
        assert editor.get("/", environ_overrides=network,
                          headers=basic("lyrics","offline-test-password-for-lan-2026")).status_code == 403
    assert editor.get("/", environ_overrides={"REMOTE_ADDR": "192.168.3.43",
                          "HTTP_HOST": "evil.example"},
                      headers=basic("lyrics","offline-test-password-for-lan-2026")).status_code == 403


def test_lan_save_requires_password_csrf_and_origin(editor, tmp_path):
    import base64
    audio_file(tmp_path)
    token=base64.b64encode(b"lyrics:offline-test-password-for-lan-2026").decode()
    headers={"Authorization":"Basic "+token}
    network={"REMOTE_ADDR":"192.168.3.50", "HTTP_HOST":"192.168.3.7:28886"}
    song=editor.get("/api/track?path=Song.flac",headers=headers,environ_overrides=network).json
    payload={"path":"Song.flac","lyrics":"[00:02.00]LAN correction",
             "expected_sha256":song["active_sha256"]}
    assert editor.post("/api/save",json=payload,headers=headers,
                       environ_overrides=network).status_code==403
    assert editor.post("/api/save",json=payload,headers={**headers,"X-CSRF-Token":CSRF,
                       "Origin":"http://evil.example"},environ_overrides=network).status_code==403
    response=editor.post("/api/save",json=payload,
                         headers={**headers,"X-CSRF-Token":CSRF,
                                  "Origin":"http://192.168.3.7:28886"},
                         environ_overrides=network)
    assert response.status_code==200,response.json
    assert response.json["writes_music"] is False
    assert confirmed_store.correction_count()==1


def test_theme_support_is_static_and_does_not_mutate_media(editor, tmp_path):
    from pathlib import Path
    audio_file(tmp_path)
    css = editor.get("/style.css")
    js = editor.get("/theme.js")
    assert css.status_code == 200
    assert b"prefers-color-scheme" in css.data
    assert b'data-theme="dark"' in css.data
    assert b"color-scheme:dark" in css.data
    assert b"localStorage" in js.data
    assert b'home-lrcapi-theme-v1' in js.data
    assert b'getElementById("theme-select")' in js.data
    assert not editor.get("/api/track?path=Song.flac").json["correction_id"]
