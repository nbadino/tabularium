"""ZIP import into a project (docs/COLLECTIONS.md).

The archive arrives in chunks (the live instance sits behind a proxy that refuses
large bodies), is inspected from its central directory before anything is
written, and is extracted entry by entry with every path checked against the
extraction folder. Images and PDFs become pages through the same registration as
the folder scan; everything else is skipped and reported.

Layout: while the top level holds exactly one folder and no file, that folder is
the root (zipping the folder *Lloyds* gives `Lloyds/…`). Two or more folders at
the root are a choice for the user: one collection per first-level folder, or all
pages together. Deeper folders belong to their first-level folder.
"""
from __future__ import annotations

import json
import re
import shutil
import stat
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .. import config
from ..adapters import collections as collectionstore
from ..db import connect
from . import images as imgmod
from . import scan as scanmod

UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
STALE_SECONDS = 24 * 3600
REPORTED_NAMES = 50
_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
_ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
_DRIVE = re.compile(r"^[A-Za-z]:")
# The ratio check only applies above this size: a small blank image compresses
# well and is not a bomb.
_RATIO_FLOOR = 1024 * 1024
_COLLECTION_NAME_MAX = 120
# Claiming an archive for import is check-then-write on upload.json.
_claim_lock = threading.Lock()


class UploadError(Exception):
    """A refusal with an i18n code and an HTTP status."""

    def __init__(self, code: str, status: int = 400, **params) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params


class TooLarge(Exception):
    """An entry wrote more bytes than allowed."""


@dataclass
class Entry:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]  # path segments, after unwrapping

    @property
    def folder(self) -> str | None:
        return self.parts[0] if len(self.parts) > 1 else None


@dataclass
class Plan:
    entries: list[Entry] = field(default_factory=list)
    root: str = ""
    skipped: list[dict] = field(default_factory=list)
    ignored: int = 0

    def folders(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            if entry.folder is not None:
                counts[entry.folder] = counts.get(entry.folder, 0) + 1
        return counts

    def to_dict(self) -> dict:
        folders = self.folders()
        reasons: dict[str, int] = {}
        for item in self.skipped:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        return {
            "root": self.root,
            "files": len(self.entries),
            "folders": [{"name": name, "files": n} for name, n in sorted(folders.items(), key=lambda kv: kv[0].lower())],
            "loose_files": sum(1 for e in self.entries if e.folder is None),
            "needs_choice": len(folders) >= 2,
            "skipped": self.skipped[:REPORTED_NAMES],
            "skipped_counts": reasons,
            "skipped_total": len(self.skipped),
            "ignored": self.ignored,
        }


# --- the upload folder ---------------------------------------------------------
def uploads_root(project_id: int) -> Path:
    return config.DATA_DIR / str(project_id) / "uploads"


def upload_dir(project_id: int, upload_id: str) -> Path:
    if not UPLOAD_ID.match(upload_id):
        raise UploadError("zip_upload_not_found", 404)
    return uploads_root(project_id) / f"zip-{upload_id}"


def _state(project_id: int, upload_id: str) -> dict:
    path = upload_dir(project_id, upload_id) / "upload.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UploadError("zip_upload_not_found", 404) from exc


def _save_state(project_id: int, upload_id: str, state: dict) -> None:
    folder = upload_dir(project_id, upload_id)
    tmp = folder / "upload.json.tmp"
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(folder / "upload.json")


def _remove_stale(project_id: int) -> None:
    """Unfinished uploads older than a day: the archive was never imported."""
    root = uploads_root(project_id)
    if not root.is_dir():
        return
    now = time.time()
    for folder in root.glob("zip-*"):
        try:
            state = json.loads((folder / "upload.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not state.get("imported") and now - float(state.get("created", now)) > STALE_SECONDS:
            shutil.rmtree(folder, ignore_errors=True)


def start_upload(project_id: int, filename: str, size: int) -> dict:
    if Path(filename).suffix.lower() != ".zip":
        raise UploadError("zip_not_zip")
    if size <= 0:
        raise UploadError("zip_empty")
    if size > config.ZIP_MAX_ARCHIVE_BYTES:
        raise UploadError("zip_too_large", 413, mb=config.ZIP_MAX_ARCHIVE_BYTES // (1024 * 1024))
    _remove_stale(project_id)
    upload_id = uuid.uuid4().hex
    folder = upload_dir(project_id, upload_id)
    folder.mkdir(parents=True)
    (folder / "archive.zip").touch()
    _save_state(project_id, upload_id, {
        "filename": Path(filename).name, "size": size, "received": 0,
        "created": time.time(), "imported": False,
    })
    return {"upload_id": upload_id, "chunk_size": config.ZIP_CHUNK_BYTES}


def append_chunk(project_id: int, upload_id: str, offset: int, data: bytes) -> dict:
    state = _state(project_id, upload_id)
    if state["imported"]:
        raise UploadError("zip_already_imported", 409)
    if offset != state["received"]:
        raise UploadError("zip_chunk_order", 409, expected=state["received"])
    if len(data) > config.ZIP_CHUNK_BYTES or state["received"] + len(data) > state["size"]:
        raise UploadError("zip_chunk_too_large", 413)
    with open(upload_dir(project_id, upload_id) / "archive.zip", "ab") as fh:
        fh.write(data)
    state["received"] += len(data)
    _save_state(project_id, upload_id, state)
    return {"received": state["received"], "size": state["size"]}


def status(project_id: int, upload_id: str) -> dict:
    """Bytes received so far, so an interrupted upload resumes where it stopped."""
    state = _state(project_id, upload_id)
    return {"received": state["received"], "size": state["size"], "imported": state["imported"]}


def cancel(project_id: int, upload_id: str) -> None:
    state = _state(project_id, upload_id)
    if state["imported"]:
        raise UploadError("zip_already_imported", 409)
    shutil.rmtree(upload_dir(project_id, upload_id), ignore_errors=True)


# --- inspection ----------------------------------------------------------------
def _safe_parts(name: str) -> tuple[str, ...] | None:
    """Path segments of an entry, or `None` when it could land outside the folder."""
    name = name.replace("\\", "/")
    if name.startswith("/") or _DRIVE.match(name) or "\0" in name:
        return None
    parts = tuple(p for p in PurePosixPath(name).parts if p not in ("", "."))
    if not parts or any(p == ".." for p in parts):
        return None
    return parts


def _is_junk(parts: tuple[str, ...]) -> bool:
    return any(p == "__MACOSX" or p.startswith(".") for p in parts) or parts[-1].lower() in _JUNK_NAMES


def _unix_type(info: zipfile.ZipInfo) -> int:
    return stat.S_IFMT(info.external_attr >> 16)


def _open_zip(project_id: int, upload_id: str) -> zipfile.ZipFile:
    state = _state(project_id, upload_id)
    if state["imported"]:
        raise UploadError("zip_already_imported", 409)
    if state["received"] != state["size"]:
        raise UploadError("zip_incomplete", 409)
    try:
        return zipfile.ZipFile(upload_dir(project_id, upload_id) / "archive.zip")
    except (zipfile.BadZipFile, OSError) as exc:
        raise UploadError("zip_invalid") from exc


def plan_archive(zf: zipfile.ZipFile) -> Plan:
    infos = zf.infolist()
    if len(infos) > config.ZIP_MAX_ENTRIES:
        raise UploadError("zip_too_many_entries", 413, n=config.ZIP_MAX_ENTRIES)
    plan = Plan()
    seen: set[tuple[str, ...]] = set()
    kept: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    total = 0

    def skip(info: zipfile.ZipInfo, reason: str) -> None:
        plan.skipped.append({"name": info.filename, "reason": reason})

    for info in infos:
        if info.is_dir():
            continue
        parts = _safe_parts(info.filename)
        if parts is None:
            skip(info, "unsafe_path")
            continue
        if _is_junk(parts):
            plan.ignored += 1
            continue
        kind = _unix_type(info)
        if kind == stat.S_IFLNK:
            skip(info, "symlink")
            continue
        if kind not in (0, stat.S_IFREG):
            skip(info, "not_regular")
            continue
        if info.flag_bits & 0x1:
            skip(info, "encrypted")
            continue
        ext = PurePosixPath(parts[-1]).suffix.lower()
        if ext in _ARCHIVE_EXTS:
            skip(info, "nested_archive")
            continue
        if ext not in imgmod.SUPPORTED_IMAGE_EXTS and ext != ".pdf":
            skip(info, "not_image")
            continue
        if info.file_size > config.ZIP_MAX_FILE_BYTES:
            skip(info, "too_large")
            continue
        if info.file_size > _RATIO_FLOOR and info.file_size > config.ZIP_MAX_RATIO * max(info.compress_size, 1):
            skip(info, "suspicious_ratio")
            continue
        key = tuple(p.lower() for p in parts)
        if key in seen:
            skip(info, "duplicate_entry")
            continue
        seen.add(key)
        total += info.file_size
        kept.append((info, parts))
    if total > config.ZIP_MAX_TOTAL_BYTES:
        raise UploadError("zip_too_large_unpacked", 413, mb=config.ZIP_MAX_TOTAL_BYTES // (1024 * 1024))

    root: list[str] = []
    while kept and all(len(parts) > 1 for _, parts in kept) and len({parts[0] for _, parts in kept}) == 1:
        root.append(kept[0][1][0])
        kept = [(info, parts[1:]) for info, parts in kept]
    plan.root = "/".join(root)
    plan.entries = [Entry(info, parts) for info, parts in kept]
    return plan


def inspect(project_id: int, upload_id: str) -> dict:
    with _open_zip(project_id, upload_id) as zf:
        return plan_archive(zf).to_dict()


# --- import ----------------------------------------------------------------------
def _copy_limited(src, target: Path, *, declared: int, budget: list[int]) -> None:
    """Stream an entry to disk, counting what is actually written.

    The declared size can lie; the bytes cannot. `budget` is the archive-wide
    allowance left, shared across entries.
    """
    limit = min(declared, config.ZIP_MAX_FILE_BYTES)
    written = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit or written > budget[0]:
                    raise TooLarge()
                out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    budget[0] -= written


def _collection_name(folder: str) -> str:
    return folder.strip()[:_COLLECTION_NAME_MAX] or folder


def run_import(
    project_id: int,
    upload_id: str,
    *,
    layout: str,
    collection_id: int | None,
    collection_name: str | None = None,
    lang: str = "it",
    on_progress=None,
) -> dict:
    """Extract and register the pages; returns the summary.

    A flat import goes into `collection_id`, or into the collection called
    `collection_name` (created if the project has none by that name), or into
    "no collection" when both are absent.
    """
    folder = upload_dir(project_id, upload_id)
    files_dir = folder / "files"
    files_dir.mkdir(exist_ok=True)
    base = files_dir.resolve()
    with _claim_lock:
        zf = _open_zip(project_id, upload_id)
        state = _state(project_id, upload_id)
        state["imported"] = True  # claimed before the work: one import per archive
        _save_state(project_id, upload_id, state)
    with zf:
        plan = plan_archive(zf)
        budget = [config.ZIP_MAX_TOTAL_BYTES]
        extracted: list[tuple[Entry, Path]] = []
        total = len(plan.entries)
        for index, entry in enumerate(plan.entries, 1):
            target = (files_dir.joinpath(*entry.parts)).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                plan.skipped.append({"name": entry.info.filename, "reason": "unsafe_path"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(entry.info) as src:
                    _copy_limited(src, target, declared=entry.info.file_size, budget=budget)
            except TooLarge:
                plan.skipped.append({"name": entry.info.filename, "reason": "too_large"})
                continue
            except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError):
                plan.skipped.append({"name": entry.info.filename, "reason": "corrupt"})
                continue
            extracted.append((entry, target))
            if on_progress is not None:
                on_progress("extracting", index, total)
    (folder / "archive.zip").unlink(missing_ok=True)

    report = scanmod.ScanReport(found_files=len(extracted))
    collections: dict[str, dict] = {}
    unassigned = 0
    flat_created = False
    with connect() as conn:
        if layout == "flat" and collection_name:
            collection_id, _name, flat_created = collectionstore.ensure(
                conn, project_id, _collection_name(collection_name),
            )
        for index, (entry, target) in enumerate(extracted, 1):
            target_collection = collection_id
            if layout == "collections":
                target_collection = None
                if entry.folder is not None:
                    key = _collection_name(entry.folder).lower()
                    if key not in collections:
                        cid, name, created = collectionstore.ensure(conn, project_id, _collection_name(entry.folder))
                        collections[key] = {"id": cid, "name": name, "created": created, "pages": 0}
                    target_collection = collections[key]["id"]
            before = report.registered
            kind = "pdf" if target.suffix.lower() == ".pdf" else "image"
            scanmod.register_candidate(
                conn, project_id, scanmod.Candidate(target, kind), files_dir, report,
                lang=lang, collection_id=target_collection,
            )
            added = report.registered - before
            if layout == "collections" and entry.folder is not None:
                collections[_collection_name(entry.folder).lower()]["pages"] += added
            elif target_collection is None:
                unassigned += added
            if on_progress is not None:
                on_progress("registering", index, len(extracted))
    if layout == "flat" and collection_id is not None:
        names = collectionstore.names(project_id)
        collections[names.get(collection_id, "")] = {
            "id": collection_id, "name": names.get(collection_id, ""), "created": flat_created,
            "pages": report.registered,
        }
    summary = plan.to_dict()
    summary.update({
        "registered": report.registered,
        "duplicates": report.duplicates,
        "unsupported": report.unsupported,
        "errors": report.errors[:REPORTED_NAMES],
        "collections": sorted(collections.values(), key=lambda c: c["name"].lower()),
        "unassigned_pages": unassigned,
    })
    return summary
