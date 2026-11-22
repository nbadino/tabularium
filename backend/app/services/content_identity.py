"""The content of a page's source file, so the same picture is never registered twice.

Every page stores the SHA-256 and byte size of its source file (`pages.content_sha256`,
`pages.file_size`; for a PDF both describe the whole PDF). An upload, a zip import or a
scan skips a file whose content is already an available page of the same project and
names it in the report, so sending a folder again only adds what is missing.

Pages registered before the hash existed get it from `backfill`, which the app runs in
a background thread at start-up. Until it has reached a page, `ProjectIndex` finds it
by its file name and size and hashes it on the spot, so the check is exact even while
the backfill is running.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections import defaultdict
from pathlib import Path, PurePosixPath

from ..adapters import pages as pagestore
from ..db import connect

log = logging.getLogger(__name__)

CHUNK = 1 << 20
# Uploads used to be saved as `000000_<name>` (a per-request counter); a clash inside one
# request still gets that prefix. The name a person gave the file is what follows it.
_UPLOAD_COUNTER = re.compile(r"^\d{6}_")


def source_name(rel_path: str) -> str:
    """The file name a person recognises: the last path part, without the upload counter."""
    return _UPLOAD_COUNTER.sub("", PurePosixPath(rel_path.replace("\\", "/")).name)


def hash_file(path: str | Path) -> tuple[str, int]:
    """SHA-256 (hex) and byte size of a file, read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class ProjectIndex:
    """The contents already in a project, loaded once per upload, scan or import."""

    def __init__(self, conn, project_id: int):
        self.conn = conn
        self.project_id = project_id
        self.hashes: set[str] = set()
        # Pages not hashed yet, by file name: the only ones that may need hashing here.
        self._unhashed: dict[str, list[tuple[str, int | None]]] = defaultdict(list)
        for row in pagestore.content_rows(conn, project_id):
            if row["content_sha256"]:
                self.hashes.add(row["content_sha256"])
            else:
                self._unhashed[source_name(row["rel_path"])].append((row["abs_path"], row["file_size"]))

    def contains(self, sha: str, name: str, size: int) -> bool:
        """True when this content is already a page; hashes same-name, same-size pages first."""
        if sha in self.hashes:
            return True
        pending = self._unhashed.get(name)
        if pending:
            keep = []
            for abs_path, known_size in pending:
                if known_size is not None and known_size != size:
                    keep.append((abs_path, known_size))
                    continue
                try:
                    if Path(abs_path).stat().st_size != size:
                        keep.append((abs_path, known_size))
                        continue
                    other_sha, other_size = hash_file(abs_path)
                except OSError:
                    keep.append((abs_path, known_size))
                    continue
                pagestore.set_content(self.conn, self.project_id, abs_path, other_sha, other_size)
                self.hashes.add(other_sha)
            self._unhashed[name] = keep
        return sha in self.hashes

    def add(self, sha: str) -> None:
        self.hashes.add(sha)


def backfill(limit: int | None = None, stop: threading.Event | None = None) -> int:
    """Hash the source files of pages that have no hash yet; returns the files hashed.

    One file at a time, committed file by file, so it can be stopped and resumed and
    never holds the write lock for long. A file that cannot be read is left as it is.
    """
    done = 0
    failed: set[str] = set()
    while limit is None or done < limit:
        if stop is not None and stop.is_set():
            break
        with connect() as conn:
            row = pagestore.next_unhashed(conn, failed)
            if row is None:
                break
            try:
                sha, size = hash_file(row["abs_path"])
            except OSError as exc:
                log.warning("content hash: cannot read %s: %s", row["abs_path"], exc)
                failed.add(row["abs_path"])
                continue
            pagestore.set_content(conn, row["project_id"], row["abs_path"], sha, size)
            conn.commit()
        done += 1
    return done


def start_backfill_thread(stop: threading.Event | None = None) -> threading.Thread:
    """Run `backfill` in a daemon thread (app start-up)."""

    def run() -> None:
        try:
            n = backfill(stop=stop)
            if n:
                log.info("content hash: backfilled %d source files", n)
        except Exception:  # noqa: BLE001 - a backfill failure must never stop the app
            log.exception("content hash backfill failed")

    thread = threading.Thread(target=run, name="content-hash-backfill", daemon=True)
    thread.start()
    return thread


def check_names(conn, project_id: int, files: list[tuple[str, int]]) -> tuple[list[int], list[int]]:
    """Split a folder's (name, size) list into the files already in the project and the rest.

    A browser asks this before sending a folder, so it uploads only what is missing.
    Name and size are what it knows without reading every file; the upload itself still
    checks the content, so a file wrongly reported missing is skipped there.
    """
    sizes: dict[str, set[int]] = defaultdict(set)
    for row in pagestore.content_rows(conn, project_id):
        size = row["file_size"]
        if size is None:
            try:
                size = Path(row["abs_path"]).stat().st_size
            except OSError:
                continue
        sizes[source_name(row["rel_path"])].add(int(size))
    present: list[int] = []
    missing: list[int] = []
    for position, (name, size) in enumerate(files):
        (present if size in sizes.get(source_name(name), ()) else missing).append(position)
    return present, missing
