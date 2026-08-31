from __future__ import annotations

from pathlib import Path

from app.services.artifacts import verify_manifest, write_manifest


def test_artifact_manifest_detects_tampering(tmp_path: Path):
    root = tmp_path / "checkpoints"
    root.mkdir()
    item = root / "adapter.bin"
    item.write_bytes(b"first")
    manifest = write_manifest(root)
    assert verify_manifest(root, manifest)["ok"] is True
    item.write_bytes(b"tampered")
    result = verify_manifest(root, manifest)
    assert result["ok"] is False
    assert result["checked"] == 1
