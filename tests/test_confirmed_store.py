import os
from pathlib import Path
import sqlite3

import pytest

from mod import confirmed_store as db


def test_metadata_confirm_show_revoke_and_audit(tmp_path):
    assert db.find(title="Hello", artist="Singer") is None
    old = db.confirm(title="Hello", artist="Singer",
                     lyrics="[00:01.00]Original", source="reviewed", reviewer="owner")
    assert db.find(title="Hello", artist="Singer")["id"] == old
    assert db.find(title="Hello", artist="Else") is None
    assert db.find(title="Hello", artist="Singer", album="Another") is None
    new = db.confirm(title="Hello", artist="Singer", lyrics="[00:01.00]Revised",
                     source="manual", reviewer="owner")
    assert new != old
    assert db.find(title="Hello", artist="Singer")["lyrics"].endswith("Revised")
    db.revoke(title="Hello", artist="Singer")
    assert db.find(title="Hello", artist="Singer") is None
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmed").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM confirmation_events").fetchone()[0] == 3
    assert db.database_path().stat().st_mode & 0o777 == 0o600


def test_file_specific_binding_detects_replacement(tmp_path):
    root = tmp_path / "music"
    file = root / "song.flac"
    file.write_bytes(b"first")
    db.confirm(path="/music/song.flac",title="Song",artist="Singer",lyrics="[00:02.00]Truth")
    assert db.find(path="/music/song.flac")["verified"]
    # A path-only confirmed record must not become a broad title+artist alias.
    assert db.find(title="Song", artist="Singer") is None
    file.write_bytes(b"replaced audio with new contents")
    assert db.find(path="/music/song.flac") is None


def test_invalid_paths_and_inputs_rejected():
    with pytest.raises(ValueError):
        db.confirm(path="/etc/passwd",lyrics="Bad")
    with pytest.raises(ValueError):
        db.confirm(title="Only Title",lyrics="Bad")
    with pytest.raises(ValueError):
        db.confirm(title="T",artist="A",lyrics="")
    with pytest.raises(ValueError):
        db.confirm(title="T",artist="A",duration="NaN",lyrics="ok")
