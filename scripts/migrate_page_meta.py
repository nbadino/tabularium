#!/usr/bin/env python3
"""Migrazione dei metadati di pagina e azzeramento delle annotazioni non valide.

Fa tre cose, tutte su dati reali, quindi di default gira in **dry-run**:

1. **Metadati da nome file.** `issue_no`, `page_no` e `page_type` vengono dedotti
   da `LSI_17186_015.tif` invece che restare vuoti. Il tipo di pagina decide la
   ricetta di annotazione (indice = una tabella; voyage-supplement = colonne di
   schede), quindi senza di esso il resto del flusso non sa cosa proporre.

2. **Data del fascicolo.** `issue_date` conteneva la data EXIF, cioè quando
   l'archivio è stato *digitalizzato* (2018), non quando il giornale è uscito
   (1973). Falsava lo split per annata, che avrebbe messo tutto il corpus in un
   unico gruppo. La si sposta in `meta_json.scan_date`, dove è un dato onesto, e
   `issue_date` torna vuota finché non viene letta dalla testata.

3. **Blocchi da rifare** (`--reset-pages`). Le pagine pre-annotate con l'OCR di
   riga hanno centinaia di blocchi `Text` con l'ordine di lettura casuale e il
   contenuto a colonne fuse: come ground truth insegnerebbero l'errore. Vanno
   rifatte con gli strumenti giusti, non corrette.

Uso:
    python3 scripts/migrate_page_meta.py                      # dry-run
    python3 scripts/migrate_page_meta.py --apply
    python3 scripts/migrate_page_meta.py --apply --reset-pages 16,17
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.services import page_meta  # noqa: E402


def backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = db_path.with_name(f"{db_path.stem}.{stamp}.bak")
    shutil.copy2(db_path, dest)
    return dest


def migrate_metadata(conn: sqlite3.Connection, apply: bool) -> list[str]:
    lines: list[str] = []
    rows = conn.execute(
        "SELECT id, rel_path, issue_date, issue_no, page_no, page_type, meta_json FROM pages"
    ).fetchall()

    for row in rows:
        meta = page_meta.parse_filename(Path(row["rel_path"]).name)
        updates = meta.as_updates()
        try:
            extra = json.loads(row["meta_json"] or "{}")
        except (TypeError, ValueError):
            extra = {}

        # La data EXIF è la data di scansione: si conserva, ma non come issue_date.
        moved = None
        if row["issue_date"]:
            extra.setdefault("scan_date", row["issue_date"])
            moved = row["issue_date"]
            updates["issue_date"] = None
        if meta.publication:
            extra["publication"] = meta.publication

        if not updates and extra == json.loads(row["meta_json"] or "{}"):
            continue

        described = ", ".join(
            f"{k}={v!r}" for k, v in updates.items() if k != "issue_date"
        )
        note = f"  pagina {row['id']:>3} {Path(row['rel_path']).name:<24} {described}"
        if moved:
            note += f"  (issue_date {moved!r} → meta.scan_date)"
        lines.append(note)

        if apply:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE pages SET {sets}, meta_json=? WHERE id=?",
                (*updates.values(), json.dumps(extra), row["id"]),
            )
    return lines


def reset_pages(conn: sqlite3.Connection, page_ids: list[int], apply: bool) -> list[str]:
    lines: list[str] = []
    for pid in page_ids:
        row = conn.execute(
            "SELECT p.rel_path, p.status, COUNT(b.id) AS n "
            "FROM pages p LEFT JOIN blocks b ON b.page_id = p.id "
            "WHERE p.id = ? GROUP BY p.id",
            (pid,),
        ).fetchone()
        if row is None:
            lines.append(f"  pagina {pid}: non trovata, saltata")
            continue
        lines.append(
            f"  pagina {pid:>3} {Path(row['rel_path']).name:<24} "
            f"elimina {row['n']} blocchi, status {row['status']!r} → 'new'"
        )
        if apply:
            # Le griglie tabella pendono da blocks con ON DELETE CASCADE.
            conn.execute("DELETE FROM blocks WHERE page_id=?", (pid,))
            conn.execute("UPDATE pages SET status='new' WHERE id=?", (pid,))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="esegue le modifiche")
    parser.add_argument(
        "--reset-pages",
        default="",
        help="id pagina separati da virgola: elimina i blocchi e riporta lo stato a 'new'",
    )
    args = parser.parse_args()

    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        print(f"DB non trovato: {db_path}")
        return 1

    page_ids = [int(x) for x in args.reset_pages.split(",") if x.strip()]

    if args.apply:
        dest = backup(db_path)
        print(f"backup: {dest}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        print("\n— metadati da nome file —")
        meta_lines = migrate_metadata(conn, args.apply)
        print("\n".join(meta_lines) if meta_lines else "  niente da aggiornare")

        if page_ids:
            print("\n— azzeramento annotazioni —")
            print("\n".join(reset_pages(conn, page_ids, args.apply)))

        if args.apply:
            conn.commit()
            print("\napplicato.")
        else:
            print("\ndry-run: nessuna modifica scritta. Aggiungi --apply per eseguire.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
