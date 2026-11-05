"""PowerShell does not stop on a failed native command, even with
`$ErrorActionPreference = "Stop"`: a failed `npm ci` or `pip install` still printed
"pronto" and exited 0. Every native call must pass its exit code on."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
# A native command: npm, or `&` on anything but another .ps1 script (which throws itself).
_NATIVE = re.compile(r"^\s*(npm\s|&\s*(?!.*\.ps1)\S)")
_CHECK = re.compile(r"\$LASTEXITCODE\s+-ne\s+0")


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.ps1")), ids=lambda p: p.name)
def test_every_native_command_checks_its_exit_code(script):
    lines = [line for line in script.read_text(encoding="utf-8").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if _NATIVE.match(line) or (".ps1" in line and line.lstrip().startswith("&")):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            assert _CHECK.search(following), f"{script.name}: no exit-code check after `{line.strip()}`"
