"""`tabularium doctor`: one verdict per line, for whoever installs the app.

A server install fails in ways the application cannot show: a read-only data
directory, a port already taken, a database that does not match the schema, a
vault key generated with another key, a runtime environment that was never
prepared. Each of them is silent until something much later breaks, and the
person who meets it is usually a sysadmin who has never run Tabularium.

This module answers one question per line — ok, warn, fail — and a hint that says
what to do. Warnings are legitimate states (no database yet, no GPU, no network);
only `fail` makes the exit code non-zero.

It never writes to the data directory except a probe file it deletes, and it never
imports the web app: `tabularium doctor` must work when the app does not start.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import config

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _check(name: str, status: str, detail: str, hint: str = "") -> Check:
    return Check(name, status, detail, hint)


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".tabularium-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def check_python() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    problem = config.python_problem()
    if problem:
        return _check("python", FAIL, problem, f"reinstall with a supported interpreter: {config.PYTHON_REINSTALL}")
    return _check("python", OK, f"{version} ({sys.executable})")


def check_data_dir() -> Check:
    root = config.ROOT_DIR
    if not root.exists():
        return _check(
            "data directory", WARN, f"{root} does not exist yet",
            "it is created on the first start; if its parent is not writable, the start fails. "
            "Checking another installation? Pass --data <folder>",
        )
    if not _writable(root):
        return _check(
            "data directory", FAIL, f"{root} is not writable",
            "give the user that runs the process write access (not root)",
        )
    return _check("data directory", OK, f"{root} (writable)")


def check_disk() -> Check:
    try:
        usage = shutil.disk_usage(config.ROOT_DIR if config.ROOT_DIR.exists() else config.ROOT_DIR.parent)
    except OSError as exc:
        return _check("free space", WARN, f"cannot be measured: {exc}")
    free_gb = usage.free / (1024 ** 3)
    detail = f"{free_gb:.1f} GiB free on {config.ROOT_DIR}"
    if free_gb < 2:
        return _check("free space", FAIL, detail, "a project and its crops need at least 2 GiB")
    if free_gb < 20:
        return _check("free space", WARN, detail, "a trained checkpoint takes several GiB")
    return _check("free space", OK, detail)


def check_database() -> Check:
    path = config.DB_PATH
    if not path.exists():
        return _check(
            "database", WARN, f"{path} does not exist",
            "it is created, with the migrations, on the first start",
        )
    try:
        from . import backup

        report = backup.integrity(path)
    except Exception as exc:  # noqa: BLE001 - an unreadable DB is a result, not a crash
        return _check("database", FAIL, f"not readable: {exc}", "check permissions and disk")
    if not report["ok"]:
        return _check(
            "database", FAIL, "integrity_check failed: " + "; ".join(report["messages"])[:200],
            "restore the latest backup from <data>/backups with `tabularium restore-db`",
        )
    return _check("database", OK, f"{path} ({report['journal_mode']})")


def check_schema() -> Check:
    path = config.DB_PATH
    if not path.exists():
        return _check("schema", WARN, "no database to check")
    try:
        from ..adapters import store

        current = store.get("schema_version", "missing")
    except Exception as exc:  # noqa: BLE001
        return _check("schema", FAIL, f"version not readable: {exc}")
    from ..db import SCHEMA_VERSION

    if str(current).isdigit() and int(current) > int(SCHEMA_VERSION):
        return _check(
            "schema", FAIL, f"{current}, newer than this release ({SCHEMA_VERSION})",
            "a later release upgraded this database: start it again, or restore the "
            "pre-upgrade backup with `tabularium restore-db`",
        )
    if str(current) != str(SCHEMA_VERSION):
        return _check(
            "schema", FAIL, f"{current} instead of {SCHEMA_VERSION}",
            "start the app once to apply the migrations; if it stays behind, the migration chain is incomplete",
        )
    return _check("schema", OK, f"version {current}")


def check_port(host: str, port: int) -> Check:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host if host not in {"0.0.0.0", "::"} else "", port))
    except OSError as exc:
        return _check(
            "port", FAIL, f"{host}:{port} not available ({exc.strerror or exc})",
            "stop the other process or use --port (if it is Tabularium itself, this is expected)",
        )
    finally:
        probe.close()
    return _check("port", OK, f"{host}:{port} free")


def check_auth() -> Check:
    if not config.auth_enabled():
        return _check(
            "authentication", WARN, "off (TABULARIUM_AUTH=off)",
            "only fine on a personal machine: anyone who reaches the port sees the projects",
        )
    path = config.DB_PATH
    if not path.exists():
        return _check("authentication", OK, "on; no users yet (set up on the first visit)")
    try:
        from ..adapters import users as userstore

        admins = userstore.count_active_admins()
        users = userstore.count_users()
    except Exception as exc:  # noqa: BLE001
        return _check("authentication", FAIL, f"users not readable: {exc}")
    if admins == 0:
        return _check(
            "authentication", FAIL, "no active administrator",
            "create the administrator on the first visit; if the instance is already set up, use `tabularium reset-password`",
        )
    return _check("authentication", OK, f"{users} users, {admins} administrators")


def check_forwarded() -> Check:
    trusted = [item.strip() for item in config.FORWARDED_ALLOW_IPS.split(",") if item.strip()]
    detail = ", ".join(trusted) or "none"
    loopback_only = trusted and all(item in {"127.0.0.1", "::1", "localhost"} for item in trusted)
    # A secure cookie means HTTPS in front: often a CDN too, whose edges would then be
    # the "client" that keys the login rate limit.
    if config.SESSION_COOKIE_SECURE and loopback_only:
        return _check(
            "trusted proxies", WARN, detail,
            "behind a CDN such as Cloudflare, add its ranges to TABULARIUM_FORWARDED_ALLOW_IPS "
            "(CIDR ranges are accepted), or the login rate limit keys on the CDN's address; "
            "ignore this if the local proxy is the only hop",
        )
    return _check("trusted proxies", OK, detail)


def check_vault() -> Check:
    if not config.DB_PATH.exists():
        # No database means no credentials to decrypt yet: the key is generated on the
        # first start, so this is not a failure of the installation.
        return _check("credential vault", WARN, "no database yet: the key is created on the first start")
    try:
        from . import vault

        problem = vault.key_problem()
    except Exception as exc:  # noqa: BLE001 - an unreadable vault is a result, not a crash
        return _check("credential vault", FAIL, f"key cannot be checked: {exc}")
    if problem:
        return _check(
            "credential vault", FAIL, problem,
            "enter the provider keys again in Settings: values saved with the old key cannot be read",
        )
    return _check("credential vault", OK, "key available")


def check_frontend() -> Check:
    dist = config.FRONTEND_DIST
    index = dist / "index.html"
    if not index.exists():
        return _check(
            "interface", FAIL, f"{index} is missing",
            "the wheel includes the interface: if it is missing, the installation is incomplete",
        )
    return _check("interface", OK, str(dist))


def check_web_concurrency() -> Check:
    raw = os.environ.get("WEB_CONCURRENCY", "").strip()
    if raw and raw.isdigit() and int(raw) > 1:
        return _check(
            "process model", FAIL, f"WEB_CONCURRENCY={raw}",
            "Tabularium runs as one process: downloads, jobs and presence live in memory",
        )
    return _check("process model", OK, "single process")


def check_gpu() -> Check:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return _check(
            "GPU", WARN, "nvidia-smi not found",
            "recognition and training run on CPU or on a remote provider; local models need CUDA",
        )
    try:
        out = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _check("GPU", WARN, f"nvidia-smi cannot run: {exc}")
    names = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    if out.returncode != 0 or not names:
        return _check("GPU", WARN, "no GPU visible")
    return _check("GPU", OK, "; ".join(names[:4]))


def check_runtimes() -> Check:
    found = sorted(path.name for path in config.ROOT_DIR.glob("*-runtime") if path.is_dir())
    if not found:
        return _check(
            "model runtimes", WARN, "no local runtime prepared",
            "they are created from the Models page on first use; a remote provider does not need them",
        )
    return _check("model runtimes", OK, ", ".join(found))


def check_huggingface() -> Check:
    base = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    try:
        with urllib.request.urlopen(base, timeout=3):  # noqa: S310 - configurable endpoint
            return _check("model downloads", OK, f"{base} reachable")
    except (urllib.error.URLError, OSError) as exc:
        return _check(
            "model downloads", WARN, f"{base} not reachable ({exc})",
            "fine if the models are already installed or imported offline",
        )


def run(host: str = "127.0.0.1", port: int = 8787) -> list[Check]:
    return [
        check_python(),
        check_data_dir(),
        check_disk(),
        check_database(),
        check_schema(),
        check_port(host, port),
        check_auth(),
        check_forwarded(),
        check_vault(),
        check_web_concurrency(),
        check_gpu(),
        check_runtimes(),
        check_huggingface(),
        check_frontend(),
    ]


def exit_code(checks: list[Check]) -> int:
    return 1 if any(check.status == FAIL for check in checks) else 0


def to_json(checks: list[Check]) -> str:
    return json.dumps([check.to_dict() for check in checks], indent=2, ensure_ascii=False)


def format_report(checks: list[Check], version: str) -> str:
    marks = {OK: "[ok]  ", WARN: "[warn]", FAIL: "[fail]"}
    width = max(len(check.name) for check in checks) if checks else 10
    source = "TABULARIUM_ROOT" if os.environ.get("TABULARIUM_ROOT", "").strip() else "default; --data to change"
    env_state = "found" if config.ENV_FILE.is_file() else "not found"
    lines = [
        f"Tabularium {version} — doctor",
        f"  data:   {config.ROOT_DIR} ({source})",
        f"  config: {config.ENV_FILE} ({env_state})",
        "",
    ]
    for check in checks:
        lines.append(f"{marks[check.status]} {check.name.ljust(width)}  {check.detail}")
        if check.hint and check.status != OK:
            lines.append(f"       {' ' * width}  → {check.hint}")
    counts = {status: sum(1 for check in checks if check.status == status) for status in (OK, WARN, FAIL)}
    lines += [
        "",
        f"{len(checks)} checks: {counts[OK]} ok, {counts[WARN]} warnings, {counts[FAIL]} errors",
    ]
    if counts[FAIL]:
        lines.append("Fix the errors before considering the installation done.")
    return "\n".join(lines)
