#!/usr/bin/env python3
"""Reset a user's password from a checkout: the same as `tabularium reset-password`.

    backend/.venv/bin/python scripts/reset_password.py              # list the users
    backend/.venv/bin/python scripts/reset_password.py admin        # ask for the new password
    backend/.venv/bin/python scripts/reset_password.py admin "new"  # password as an argument

The data root follows TABULARIUM_ROOT (default: <repo>/data).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.cli import main  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2 and not args[1].startswith("-"):
        args = [args[0], "--password", args[1], *args[2:]]
    raise SystemExit(main(["reset-password", *args]))
