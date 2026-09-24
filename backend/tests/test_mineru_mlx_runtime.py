from __future__ import annotations

import json

from app.services import mineru_mlx_runtime


def test_mineru_mlx_runtime_isolated_and_pinned_to_the_official_extra():
    assert "mineru-vl-utils[mlx]==2.0.5" in mineru_mlx_runtime.PACKAGES


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
