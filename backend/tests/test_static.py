"""Undefined names are bugs that only fire on the code path that reaches them.

The 0.4.0 refactor left four behind (training start, first thumbnail, custom model
deletion, a type annotation); none was on a tested path. pyflakes sees them all.
"""
from __future__ import annotations

from pathlib import Path

from pyflakes.api import checkPath
from pyflakes.reporter import Reporter
import io

APP = Path(__file__).resolve().parents[1] / "app"


def test_the_backend_has_no_undefined_names() -> None:
    out, err = io.StringIO(), io.StringIO()
    for path in sorted(APP.rglob("*.py")):
        checkPath(str(path), Reporter(out, err))
    undefined = [line for line in out.getvalue().splitlines() if "undefined name" in line]
    assert not undefined and not err.getvalue(), "\n".join(undefined) + err.getvalue()


def test_the_reference_checkpoint_is_named_in_one_place() -> None:
    # Failure mode 4: the checkpoint and its pixel cap lived in eight files.
    owner = APP / "models" / "monkeyocrv2.py"
    offenders = []
    for path in APP.rglob("*.py"):
        if path == owner or path.name == "config.py":  # config: env default, cannot import models
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if '"zenosai/MonkeyOCRv2-B-Parsing"' in code or "'zenosai/MonkeyOCRv2-B-Parsing'" in code \
                    or "1003520" in code or "1_003_520" in code:
                offenders.append(f"{path.relative_to(APP)}:{number}")
    assert not offenders, offenders
