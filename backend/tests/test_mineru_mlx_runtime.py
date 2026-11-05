from __future__ import annotations

import json

from app.services import mineru_mlx_runtime


def test_mineru_mlx_runtime_isolated_and_pinned_to_the_official_extra():
    assert "mineru-vl-utils[mlx]==2.0.8" in mineru_mlx_runtime.PACKAGES


def test_mineru_mlx_runtime_launches_the_native_gateway_with_model_settings(monkeypatch):
    monkeypatch.setattr(mineru_mlx_runtime, "python_bin", lambda: __import__("pathlib").Path("/tmp/mineru-python"))
    argv = mineru_mlx_runtime.serve_argv(
        "opendatalab/MinerU2.5-Pro-2605-1.2B",
        port=18888,
        settings={
            "workflow": {"layout_image_size": [1200, 1200]},
            "serving": {"max_num_seqs": 4},
        },
    )
    assert argv[0] == "/tmp/mineru-python"
    assert argv[1].endswith("scripts/local/mineru_mlx_gateway.py")
    assert argv[argv.index("--model") + 1] == "opendatalab/MinerU2.5-Pro-2605-1.2B"
    assert argv[argv.index("--port") + 1] == "18888"
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["workflow"]["layout_image_size"] == [1200, 1200]
    assert settings["serving"]["max_num_seqs"] == 4


def test_readiness_probe_checks_the_version_that_is_installed(monkeypatch, tmp_path):
    """A probe pinned to another version than PACKAGES never passes, so every
    ensure_ready() wiped and reinstalled the venv, then failed."""
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(mineru_mlx_runtime, "python_bin", lambda: python)
    seen: list[str] = []

    def fake_run(argv, **_kwargs):
        seen.append(argv[2])
        return __import__("subprocess").CompletedProcess(argv, 0)

    monkeypatch.setattr(mineru_mlx_runtime.subprocess, "run", fake_run)
    assert mineru_mlx_runtime.is_ready()
    pinned = next(p for p in mineru_mlx_runtime.PACKAGES if p.startswith("mineru-vl-utils")).split("==")[1]
    assert f"=='{pinned}'" in seen[0]
