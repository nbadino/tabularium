"""Manifest e verifica degli artefatti prodotti da una run."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(root: Path, manifest_path: Path | None = None) -> Path:
    root = root.resolve()
    target = manifest_path or (root.parent / "artifacts.sha256")
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if root.is_dir():
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            lines.append(f"{sha256(path)}  {path.relative_to(root).as_posix()}\n")
    target.write_text("".join(lines), encoding="utf-8")
    return target


def verify_manifest(root: Path, manifest_path: Path) -> dict:
    checked = 0
    errors: list[str] = []
    if not manifest_path.is_file():
        return {"ok": False, "checked": 0, "errors": ["manifest artefatti assente"]}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            errors.append(f"riga manifest non valida: {line[:80]}")
            continue
        path = (root / relative).resolve()
        if root.resolve() not in path.parents:
            errors.append(f"percorso artefatto fuori root: {relative}")
            continue
        if not path.is_file():
            errors.append(f"artefatto mancante: {relative}")
            continue
        checked += 1
        actual = sha256(path)
        if actual != expected:
            errors.append(f"checksum errato: {relative}")
    return {"ok": not errors, "checked": checked, "errors": errors}
