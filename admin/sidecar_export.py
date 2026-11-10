"""Optional, explicit, reversible export of one SQLite correction to a NAS .lrc.

No automatic export, no arbitrary upload, never modifies audio tags.
The original file is backed up to Mac local APFS before any NAS write.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

from mod import confirmed_store as store


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _target(relative: str):
    from admin.server import _resolve, _relative
    audio = _resolve(relative, audio=True)
    target = audio.with_suffix(".lrc")
    if target.is_symlink():
        raise ValueError("Refusing to overwrite symlinked .lrc")
    if target.exists() and not target.is_file():
        raise ValueError("The sidecar destination is not a file")
    if _relative(target).startswith("../"):
        raise ValueError("Invalid sidecar destination")
    return audio, target


def _require_nfs():
    """Fail closed unless the expected NAS v2 is truly mounted as NFS."""
    mountpoint = "/Users/Shared/nfs/v2"
    actual = os.environ.get("HOME_SERVER_MUSIC_ROOT", "")
    if not actual or not Path(actual).resolve().is_relative_to(Path(mountpoint).resolve()):
        raise ValueError("The music root is not beneath the approved NAS NFS mount")
    mounts = subprocess.run(["/sbin/mount"], capture_output=True, text=True,
                            check=True, timeout=5).stdout
    expected = " on " + mountpoint + " (nfs,"
    if expected not in mounts:
        raise ValueError("Expected NAS v2 NFS is not mounted; refusing writes")


def _backup_directory() -> Path:
    root = Path.home() / "Library/Application Support/HomeServer/LrcAPI/sidecar-backups"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise ValueError("Backup directory cannot be a symlink")
    os.chmod(root, 0o700)
    return root


def _atomic_write(path: Path, data: bytes):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix="." + path.stem + ".",
                                         suffix=".lrc.tmp", dir=path.parent,
                                         delete=False) as out:
            temporary = Path(out.name)
            os.chmod(temporary, 0o644)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        # Deliberately rename only the exact audio's sidecar. Never the audio itself.
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _latest_export(track: Path):
    if not store.database_path().is_file():
        return None
    with store.connect() as conn:
        row = conn.execute(
            "SELECT * FROM sidecar_exports WHERE track_path=? AND status='exported' "
            "ORDER BY id DESC LIMIT 1", (str(track),)).fetchone()
        return dict(row) if row else None


def status(relative: str):
    audio, target = _target(relative)
    result = _latest_export(audio)
    if not result:
        return None
    # Never claim a sidecar is still synced after another tool changed it.
    try:
        data = target.read_bytes()
        if len(data) > 200000 or _hash(data) != result["after_sha256"]:
            return {"status": "changed_since_export", "id": result["id"]}
    except OSError:
        return {"status": "missing_after_export", "id": result["id"]}
    return {"status": "exported", "id": result["id"], "at": result["timestamp"]}


def export(relative: str) -> dict:
    _require_nfs()
    audio, destination = _target(relative)
    from admin.server import _current
    song = _current(relative)
    if song["status"] != "corrected":
        raise ValueError("Only an explicit SQLite correction may be exported")
    if status(relative) and status(relative)["status"] == "exported":
        raise ValueError("Already exported; restore the previous export before exporting again")
    payload = song["active"].encode("utf-8")
    if len(payload) > 200000:
        raise ValueError("Lyric text too large")
    old = destination.read_bytes() if destination.exists() else None
    if old is not None and len(old) > 200000:
        raise ValueError("Original .lrc too large to safely back up")
    if old is not None and old == payload:
        return {"status": "already_matching", "writes_music": False}
    backups = _backup_directory()
    backup = None
    if old is not None:
        # Unique backup in Mac local APFS, never place backups on degraded NAS.
        name = _hash((str(audio) + str(time.time_ns())).encode()) + ".lrc"
        backup = backups / name
        with backup.open("xb") as out:
            os.chmod(backup, 0o600)
            out.write(old)
            out.flush()
            os.fsync(out.fileno())

    before_digest = _hash(old) if old is not None else None
    after_digest = _hash(payload)
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO sidecar_exports (confirmed_id,track_path,lrc_path,before_sha256,"
            "after_sha256,backup_path,original_exists,status,timestamp) "
            "VALUES(?,?,?,?,?,?,?,'pending',?)",
            (song["correction_id"], str(audio), str(destination), before_digest,
             after_digest, str(backup) if backup else None, int(old is not None), int(time.time())),
        )
        export_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Safety: if SQLite audit cannot be created, no NAS write is attempted.
    try:
        _atomic_write(destination, payload)
    except OSError:
        # The pending entry remains visible for inspection and recovery.
        raise
    with store.connect() as conn:
        conn.execute("UPDATE sidecar_exports SET status='exported' WHERE id=?", (export_id,))
    return {"status": "exported", "id": export_id, "writes_music": True,
            "backup_local": bool(backup)}


def restore(relative: str) -> dict:
    _require_nfs()
    audio, destination = _target(relative)
    result = _latest_export(audio)
    if not result:
        raise ValueError("No active exported correction to restore")
    current = destination.read_bytes() if destination.exists() else None
    if current is None or _hash(current) != result["after_sha256"]:
        raise ValueError("The .lrc file changed after export; refusing to overwrite it")
    if result["original_exists"]:
        backup = Path(result["backup_path"] or "")
        backup_root = _backup_directory().resolve()
        if not backup.resolve().is_relative_to(backup_root) or not backup.is_file():
            raise ValueError("Local backup is unavailable or outside the backup directory")
        original = backup.read_bytes()
        if _hash(original) != result["before_sha256"]:
            raise ValueError("Backup checksum mismatch")
        _atomic_write(destination, original)
    else:
        destination.unlink()
    with store.connect() as conn:
        conn.execute(
            "UPDATE sidecar_exports SET status='restored',restored_at=? WHERE id=?",
            (int(time.time()), result["id"]))
    return {"status": "restored", "writes_music": True}
