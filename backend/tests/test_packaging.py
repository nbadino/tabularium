"""Il pacchetto installabile (`pyproject.toml`) resta allineato al checkout."""
from __future__ import annotations

import tomllib
from pathlib import Path

from app import config

REPO = Path(__file__).resolve().parents[2]


def _requirements() -> set[str]:
    lines = (REPO / "backend" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def test_pyproject_dependencies_match_requirements():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert set(project["dependencies"]) == _requirements()


def test_wheel_bundles_every_resource_the_backend_reads():
    """Ogni risorsa letta da RESOURCE_DIR deve finire in `app/_bundle/`."""
    build = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["build"]
    bundled = build["targets"]["wheel"]["force-include"]
    for source in ("VERSION", "frontend/dist", "conf", "scripts/cloud", "scripts/local", "scripts/serve_model.sh"):
        assert bundled.get(source) == f"app/_bundle/{source}"


def test_checkout_uses_the_repository_as_resource_dir():
    assert config.INSTALLED is False
    assert config.RESOURCE_DIR == REPO
    assert config.VERSION == (REPO / "VERSION").read_text(encoding="utf-8").strip()


def test_every_version_field_matches_the_version_file():
    # R14: scripts/version.py bump is the only writer; a field it forgets drifts.
    import json
    import re

    version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    assert json.loads((REPO / "frontend/package.json").read_text())["version"] == version
    citation = (REPO / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(r'^version: "?([^"\n]+)"?$', citation, re.M).group(1) == version


def test_the_lock_pins_every_requirement_within_its_range():
    # constraints.txt is what images and CI install: it must satisfy requirements.txt.
    from packaging.requirements import Requirement

    pins = {}
    for line in (REPO / "backend/constraints.txt").read_text().splitlines():
        if "==" in line and not line.startswith((" ", "#")):
            name, version = line.split(";")[0].strip().split("==")
            pins.setdefault(name.lower().replace("_", "-"), []).append(version)
    for line in (REPO / "backend/requirements.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            req = Requirement(line)
            versions = pins.get(req.name.lower().replace("_", "-"))
            assert versions, f"{req.name} is not pinned in constraints.txt"
            assert all(req.specifier.contains(v) for v in versions), (req, versions)
