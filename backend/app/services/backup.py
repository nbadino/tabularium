"""Backup SQLite online, verifica e ripristino controllato."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .. import config


def backup_dir() -> Path:
    path = config.ROOT_DIR / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def integrity(path: Path | None = None) -> dict:
    target = path or config.DB_PATH
    with sqlite3.connect(target) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchall()
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    ok = len(result) == 1 and result[0][0] == "ok"
    return {"ok": ok, "journal_mode": journal, "messages": [row[0] for row in result]}


def create_backup(*, reason: str = "manual") -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir() / f"tabularium-{stamp}-{reason}.db"
    # Connection.backup include correttamente WAL e non richiede di copiare
    # separatamente .wal/.shm mentre il backend sta lavorando.
    source = sqlite3.connect(config.DB_PATH)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
        dest.commit()
    finally:
        dest.close()
        source.close()
    check = integrity(target)
    if not check["ok"]:
        target.unlink(missing_ok=True)
        raise RuntimeError("backup non integro")
    for old in sorted(backup_dir().glob("tabularium-*.db"), reverse=True)[config.BACKUP_RETENTION:]:
        old.unlink(missing_ok=True)
    return {"path": str(target), "name": target.name, "size": target.stat().st_size, "reason": reason, **check}


def list_backups() -> list[dict]:
    out = []
    for path in sorted(backup_dir().glob("tabularium-*.db"), reverse=True):
        try:
            out.append({"name": path.name, "size": path.stat().st_size, "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
        except OSError:
            continue
    return out


def restore(name: str) -> dict:
    candidate = (backup_dir() / name).resolve()
    if candidate.parent != backup_dir().resolve() or candidate.name != name or not candidate.is_file():
        raise ValueError("backup non valido")
    check = integrity(candidate)
    if not check["ok"]:
        raise ValueError("backup non integro")
    # Il chiamante crea il pre-backup prima di invocare questa funzione.
    source = sqlite3.connect(candidate)
    dest = sqlite3.connect(config.DB_PATH)
    try:
        source.backup(dest)
        dest.commit()
    finally:
        dest.close()
        source.close()
    return {"restored": candidate.name, **integrity()}
