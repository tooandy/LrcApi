"""Small local SQLite store for manually corrected / overridden lyrics.

Local .lrc / embedded lyrics are accepted by default. SQLite only holds
exceptions, manual fixes and their full history, on Mac local APFS.
Only the loopback-only editor may write records. The public API stays read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import sys
import time
import unicodedata


def database_path() -> Path:
    return Path(os.environ.get(
        "LRCAPI_DB_PATH",
        str(Path.home() / "Library/Application Support/HomeServer/LrcAPI/overrides.sqlite3"),
    )).expanduser()


def _normal(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "").strip()).casefold()


def identity_keys(*, path: str = "", title: str = "", artist: str = "",
                  album: str = "", duration: str | float | None = None) -> list[str]:
    """Exact metadata key or a file-specific key. Do not guess versions."""
    keys = []
    if path:
        root = os.environ.get("HOME_SERVER_MUSIC_ROOT")
        if not root:
            raise ValueError("Music root must be configured for path identity")
        root = os.path.realpath(root)
        # Same local_music_path rules, including symlink traversal protection.
        for prefix in ("/music", "/mnt/v2/Music", root):
            if path == prefix or path.startswith(prefix + os.sep):
                relative = os.path.relpath(path, prefix)
                canonical = os.path.realpath(os.path.join(root, relative))
                if canonical != root and canonical.startswith(root + os.sep):
                    keys.append("path:" + hashlib.sha256(
                        os.path.relpath(canonical, root).encode("utf-8")).hexdigest())
                    break
        if not keys:
            raise ValueError("Requested track is outside the music root")
    if _normal(title) and _normal(artist) and (not path or (album and duration not in ("", None))):
        duration_seconds = None
        if duration not in ("", None):
            try:
                duration_seconds = int(round(float(duration)))
            except (ValueError, OverflowError, TypeError):
                raise ValueError("Invalid duration") from None
            if not (0 < duration_seconds <= 36000):
                raise ValueError("Invalid duration")
        payload = [_normal(title), _normal(artist), _normal(album), duration_seconds]
        keys.append("meta:" + hashlib.sha256(json.dumps(
            payload, ensure_ascii=False).encode("utf-8")).hexdigest())
    return keys


_SCHEMA = """
CREATE TABLE IF NOT EXISTS confirmed (
    id INTEGER PRIMARY KEY,
    lyrics TEXT NOT NULL,
    lyrics_sha256 TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('synced', 'plain')),
    source TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'revoked')),
    canonical_path TEXT,
    file_size INTEGER,
    file_mtime_ns INTEGER,
    title TEXT,
    artist TEXT,
    album TEXT,
    duration REAL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_aliases (
    key TEXT PRIMARY KEY,
    confirmed_id INTEGER NOT NULL REFERENCES confirmed(id)
);
CREATE INDEX IF NOT EXISTS idx_identity_id ON identity_aliases(confirmed_id);
CREATE TABLE IF NOT EXISTS confirmation_events (
    id INTEGER PRIMARY KEY,
    confirmed_id INTEGER NOT NULL REFERENCES confirmed(id),
    event TEXT NOT NULL CHECK (event IN ('confirm', 'reconfirm', 'revoke')),
    actor TEXT NOT NULL,
    content_sha256 TEXT,
    timestamp INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sidecar_exports (
    id INTEGER PRIMARY KEY,
    confirmed_id INTEGER NOT NULL REFERENCES confirmed(id),
    track_path TEXT NOT NULL,
    lrc_path TEXT NOT NULL,
    before_sha256 TEXT,
    after_sha256 TEXT NOT NULL,
    backup_path TEXT,
    original_exists INTEGER NOT NULL CHECK (original_exists IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('pending','exported','restored')),
    timestamp INTEGER NOT NULL,
    restored_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_export_track ON sidecar_exports(track_path,id);
"""


_INIT_LOCK = threading.Lock()
_INITIALIZED = set()


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.is_symlink():
        raise ValueError("Refusing to open a symlinked correction database")
    with _INIT_LOCK:
        if str(path) not in _INITIALIZED or not path.is_file():
            with sqlite3.connect(str(path), timeout=5) as init:
                init.execute("PRAGMA journal_mode=WAL")
                init.execute("PRAGMA foreign_keys=ON")
                init.executescript(_SCHEMA)
            os.chmod(path, 0o600)
            _INITIALIZED.add(str(path))
    conn = sqlite3.connect(str(path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _file_binding(path: str) -> tuple[str | None, int | None, int | None]:
    if not path:
        return (None, None, None)
    from api.lyrics import local_music_path
    target = local_music_path(path)
    if not target or not os.path.isfile(target):
        raise ValueError("The audio file is not readable within the music root")
    state = os.stat(target)
    return (target, state.st_size, state.st_mtime_ns)


def find(*, path: str = "", title: str = "", artist: str = "",
         album: str = "", duration: str | float | None = None) -> dict | None:
    try:
        keys = identity_keys(path=path, title=title, artist=artist, album=album, duration=duration)
    except ValueError:
        return None
    if not keys or not database_path().is_file():
        return None
    with connect() as conn:
        for key in keys:
            item = conn.execute(
                "SELECT c.* FROM identity_aliases a JOIN confirmed c ON c.id=a.confirmed_id "
                "WHERE a.key=? AND c.status='confirmed'", (key,)).fetchone()
            if item is None:
                continue
            # File-specific confirmations are invalidated when audio is replaced.
            if key.startswith("path:"):
                try:
                    current_path, size, mtime_ns = _file_binding(path)
                except (OSError, ValueError):
                    continue
                if (current_path, size, mtime_ns) != (
                        item["canonical_path"], item["file_size"], item["file_mtime_ns"]):
                    continue
            return {
                "id": item["id"], "title": item["title"] or title,
                "artist": item["artist"] or artist, "album": item["album"] or album,
                "lyrics": item["lyrics"], "kind": item["kind"],
                "source": "confirmed_db", "origin": item["source"],
                "verified": True, "sha256": item["lyrics_sha256"],
                "confirmed_at": item["updated_at"],
            }
    return None


def confirm(*, lyrics: str, title: str = "", artist: str = "", album: str = "",
            duration: str | float | None = None, path: str = "",
            source: str = "manual", reviewer: str = "owner") -> int:
    if not reviewer or not reviewer.strip() or not source or len(source) > 100:
        raise ValueError("source and reviewer required")
    if not isinstance(lyrics, str) or not (1 <= len(lyrics) <= 200000):
        raise ValueError("Invalid lyric length")
    keys = identity_keys(path=path, title=title, artist=artist, album=album, duration=duration)
    if not keys:
        raise ValueError("Supply a music-root path OR both title and artist")
    canonical_path, size, mtime_ns = _file_binding(path) if path else (None, None, None)
    kind = "synced" if re.search(r"\[\d{1,3}:\d{2}(?:[.:]\d+)?\]", lyrics) else "plain"
    digest = hashlib.sha256(lyrics.encode("utf-8")).hexdigest()
    now = int(time.time())
    with connect() as conn:
        # Saving a correction creates a new version. Original .lrc stays unchanged.
        # All aliases must refer to the same row to avoid silent overwrites.
        rows = conn.execute(
            "SELECT DISTINCT confirmed_id FROM identity_aliases WHERE key IN (" +
            ",".join("?" for _ in keys) + ")", keys,
        ).fetchall()
        ids = {int(row[0]) for row in rows}
        if len(ids) > 1:
            raise ValueError("Identity aliases conflict: human resolution required")
        old_id = next(iter(ids)) if ids else None
        conn.execute(
            "INSERT INTO confirmed(lyrics,lyrics_sha256,kind,source,reviewer,status,"
            "canonical_path,file_size,file_mtime_ns,title,artist,album,duration,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'confirmed',?,?,?,?,?,?,?,?,?)",
            (lyrics, digest, kind, source, reviewer, canonical_path, size, mtime_ns,
             title, artist, album, float(duration) if duration not in ("", None) else None, now, now),
        )
        new_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        if old_id is not None:
            conn.execute("UPDATE confirmed SET status='revoked',updated_at=? WHERE id=?", (now, old_id))
            # Rebind existing aliases; version history stays immutable.
            conn.execute("UPDATE identity_aliases SET confirmed_id=? WHERE confirmed_id=?", (new_id, old_id))
        for key in keys:
            conn.execute("INSERT OR REPLACE INTO identity_aliases(key,confirmed_id) VALUES(?,?)",
                         (key, new_id))
        conn.execute("INSERT INTO confirmation_events(confirmed_id,event,actor,content_sha256,timestamp)"
                     " VALUES(?,?,?,?,?)", (new_id, "reconfirm" if old_id else "confirm", reviewer, digest, now))
        return new_id


def revoke(*, path: str = "", title: str = "", artist: str = "", album: str = "",
           duration: str | float | None = None, reviewer: str = "owner") -> int:
    keys = identity_keys(path=path, title=title, artist=artist, album=album, duration=duration)
    if not keys:
        raise ValueError("Specify an identity to revoke")
    with connect() as conn:
        row = None
        for key in keys:
            row = conn.execute(
                "SELECT c.id, c.lyrics_sha256 FROM identity_aliases a JOIN confirmed c "
                "ON c.id=a.confirmed_id WHERE a.key=? AND c.status='confirmed'", (key,)
            ).fetchone()
            if row:
                break
        if not row:
            raise ValueError("No confirmed lyric found for this identity")
        conn.execute("UPDATE confirmed SET status='revoked',updated_at=? WHERE id=?",
                     (int(time.time()), row["id"]))
        conn.execute("INSERT INTO confirmation_events(confirmed_id,event,actor,content_sha256,timestamp)"
                     " VALUES(?,?,?,?,?)",
                     (row["id"], "revoke", reviewer, row["lyrics_sha256"], int(time.time())))
        return int(row["id"])



def history(*, path: str = "", title: str = "", artist: str = "",
            album: str = "", duration: str | float | None = None, limit: int = 15) -> list[dict]:
    keys = identity_keys(path=path, title=title, artist=artist, album=album, duration=duration)
    if not keys or not database_path().is_file():
        return []
    with connect() as conn:
        ids = set()
        for key in keys:
            row = conn.execute("SELECT confirmed_id FROM identity_aliases WHERE key=?", (key,)).fetchone()
            if row:
                ids.add(row["confirmed_id"])
        if not ids:
            return []
        # Each revision is kept by its row ID; use track identity association through
        # the historical table if the alias has been updated.
        current = next(iter(ids))
        current_row = conn.execute("SELECT canonical_path,title,artist,album FROM confirmed WHERE id=?",
                                   (current,)).fetchone()
        if not current_row:
            return []
        if current_row["canonical_path"]:
            rows = conn.execute(
                "SELECT id,source,reviewer,kind,status,lyrics_sha256,created_at,updated_at "
                "FROM confirmed WHERE canonical_path=? ORDER BY id DESC LIMIT ?",
                (current_row["canonical_path"], min(int(limit), 50))).fetchall()
        else:
            rows = conn.execute(
                "SELECT id,source,reviewer,kind,status,lyrics_sha256,created_at,updated_at "
                "FROM confirmed WHERE canonical_path IS NULL AND title=? AND artist=? AND album=? "
                "ORDER BY id DESC LIMIT ?",
                (current_row["title"], current_row["artist"], current_row["album"],
                 min(int(limit), 50))).fetchall()
        return [dict(row) for row in rows]


def correction_count() -> int:
    if not database_path().is_file():
        return 0
    with connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM confirmed WHERE status='confirmed'").fetchone()[0])

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Owner-only SQLite lyric corrections database")
    command = parser.add_subparsers(dest="command", required=True)
    for name in ("confirm", "revoke", "show"):
        p = command.add_parser(name)
        p.add_argument("--path", default="")
        p.add_argument("--title", default="")
        p.add_argument("--artist", default="")
        p.add_argument("--album", default="")
        p.add_argument("--duration", default="")
        if name == "confirm":
            p.add_argument("--lrc-file", required=True, type=Path)
            p.add_argument("--source", default="manual")
            p.add_argument("--reviewer", default="owner")
        if name == "revoke":
            p.add_argument("--reviewer", default="owner")
    args = parser.parse_args(argv)
    common = {name: getattr(args, name) for name in ("path", "title", "artist", "album", "duration")}
    try:
        if args.command == "confirm":
            lyric_text = args.lrc_file.read_text(encoding="utf-8-sig")
            ident = confirm(**common, lyrics=lyric_text, source=args.source, reviewer=args.reviewer)
            print(f"Confirmed record {ident}: SQLite updated; audio and .lrc files unchanged.")
        elif args.command == "revoke":
            ident = revoke(**common, reviewer=args.reviewer)
            print(f"Revoked confirmed record {ident}; kept history.")
        else:
            result = find(**common)
            if result:
                print(json.dumps({k: v for k, v in result.items() if k != "lyrics"}, ensure_ascii=False))
            else:
                print("No manual override; local sidecar/embedded lyrics are used by default.")
        return 0
    except (ValueError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
