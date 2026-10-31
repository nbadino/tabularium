#!/usr/bin/env python3
"""Show or bump Tabularium's shared SemVer version."""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def read_version() -> tuple[str, tuple[int, int, int]]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    match = SEMVER.fullmatch(version)
    if not match:
        raise SystemExit(f"VERSION is not valid SemVer: {version!r}")
    return version, tuple(map(int, match.groups()))


def write_version(version: str, previous: str | None = None) -> None:
    (ROOT / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    for name in ("package.json", "package-lock.json"):
        path = ROOT / "frontend" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = version
        if name == "package-lock.json":
            data["packages"][""]["version"] = version
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    citation = ROOT / "CITATION.cff"
    text = re.sub(r'^version: .*$', f'version: "{version}"', citation.read_text(encoding="utf-8"), flags=re.M)
    text = re.sub(r'^date-released: .*$', f'date-released: "{date.today().isoformat()}"', text, flags=re.M)
    citation.write_text(text, encoding="utf-8")
    changelog = ROOT / "CHANGELOG.md"
    content = changelog.read_text(encoding="utf-8")
    content = content.replace(
        "## [Unreleased]",
        f"## [Unreleased]\n\n## [{version}] - {date.today().isoformat()}",
        1,
    )
    if previous:
        # Keep the Keep-a-Changelog compare links in step with the headings.
        base = "https://github.com/nbadino/tabularium"
        content = content.replace(
            f"[Unreleased]: {base}/compare/v{previous}...HEAD",
            f"[Unreleased]: {base}/compare/v{version}...HEAD\n"
            f"[{version}]: {base}/compare/v{previous}...v{version}",
            1,
        )
    changelog.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("show", "bump"), nargs="?", default="show")
    parser.add_argument("part", choices=("patch", "minor", "major"), nargs="?")
    args = parser.parse_args()
    current, numbers = read_version()
    if args.command == "show":
        if args.part:
            parser.error("'part' is only used with bump")
        print(current)
        return
    if not args.part:
        parser.error("specify patch, minor or major")
    major, minor, patch = numbers
    if args.part == "major":
        numbers = (major + 1, 0, 0)
    elif args.part == "minor":
        numbers = (major, minor + 1, 0)
    else:
        numbers = (major, minor, patch + 1)
    updated = ".".join(map(str, numbers))
    write_version(updated, current)
    print(f"{current} → {updated}")
    print("Move the [Unreleased] notes in CHANGELOG.md, review the changes, then tag.")


if __name__ == "__main__":
    main()
