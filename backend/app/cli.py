"""Comando `tabularium`: avvia l'app installata come pacchetto.

È l'ingresso di `uv tool install tabularium` / `pipx install tabularium`:
un solo processo serve API e interfaccia, come `scripts/run.sh` da un
checkout, e quando il server risponde apre il browser.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser


def _open_when_ready(url: str, target: str | None = None, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2):  # noqa: S310
                webbrowser.open(target or url)
                return
        except OSError:
            time.sleep(0.5)


def _doctor(argv: list[str]) -> int:
    """`tabularium doctor`: check the installation without starting the app."""
    parser = argparse.ArgumentParser(
        prog="tabularium doctor",
        description="Check this installation: data directory, database, port, auth, GPU, runtimes.",
    )
    parser.add_argument("--host", default=os.environ.get("TABULARIUM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TABULARIUM_PORT", "8787")))
    parser.add_argument("--data", metavar="DIR", help="data folder (default: ~/Tabularium)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    from . import config
    from .services import doctor

    checks = doctor.run(args.host, args.port)
    if args.json:
        print(doctor.to_json(checks))
    else:
        print(doctor.format_report(checks, config.VERSION))
    return doctor.exit_code(checks)


def _models(argv: list[str]) -> int:
    """`tabularium models`: install a checkpoint without a network, list what is there."""
    parser = argparse.ArgumentParser(
        prog="tabularium models",
        description="Install and inspect model checkpoints.",
    )
    parser.add_argument("--data", metavar="DIR", help="data folder (default: ~/Tabularium)")
    commands = parser.add_subparsers(dest="command", required=True)

    importer = commands.add_parser(
        "import",
        help="install a checkpoint from a local directory (air-gapped hosts)",
        description=(
            "Copy a checkpoint directory into <data>/models/<adapter> and mark it installed, "
            "exactly as a finished download would. The directory must contain config.json and "
            "the weights."
        ),
    )
    importer.add_argument("source", help="directory with config.json and *.safetensors/*.bin")
    importer.add_argument("--adapter", required=True, help="adapter id, e.g. monkeyocrv2-parsing")
    importer.add_argument("--force", action="store_true", help="replace an existing installation")

    lister = commands.add_parser("list", help="what is installed, and where")
    lister.add_argument("--json", action="store_true", help="machine-readable output")

    args = parser.parse_args(argv)
    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    from . import config
    from .services import model_adapters, model_registry

    if args.command == "import":
        try:
            state = model_registry.import_local(args.adapter, args.source, force=args.force)
        except (ValueError, OSError) as exc:
            print(f"tabularium models import: {exc}", file=sys.stderr)
            return 2
        size_gb = (state.get("size_bytes") or 0) / (1024 ** 3)
        print(f"{args.adapter}: installed in {state['path']} ({size_gb:.2f} GiB)")
        print("The registry now sees it as a normal installation; no download will be attempted.")
        return 0

    rows = []
    for cap in model_adapters.list_adapters():
        state = model_registry.install_state(cap["adapter_id"])
        rows.append({
            "adapter_id": cap["adapter_id"],
            "display_name": cap.get("display_name"),
            "installed": bool(state.get("installed")),
            "size_bytes": state.get("size_bytes") or 0,
            "path": state.get("path"),
        })
    if args.json:
        import json

        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    width = max(len(row["display_name"] or row["adapter_id"]) for row in rows)
    print(f"Tabularium {config.VERSION} — models in {config.MODELS_DIR}")
    for row in rows:
        mark = "[ok]  " if row["installed"] else "[ -- ]"
        size = f"{row['size_bytes'] / (1024 ** 3):.2f} GiB" if row["installed"] else ""
        print(f"{mark} {(row['display_name'] or row['adapter_id']).ljust(width)}  {size}")
    return 0


# Rebuildable or regenerated content, left out of `tabularium backup`.
_BACKUP_SKIP_SUFFIXES = ("-runtime",)
_BACKUP_SKIP_NAMES = {"vendor", "cache", "tiles", "tabularium.db", "tabularium.db-wal", "tabularium.db-shm"}


def _backup(argv: list[str]) -> int:
    """`tabularium backup`: the data folder and the vault key in one archive."""
    parser = argparse.ArgumentParser(
        prog="tabularium backup",
        description=(
            "Write the data folder and the .env holding the vault key to one .tar.gz. "
            "The database is copied through SQLite's backup API, so the app may keep "
            "running. Model runtimes, caches and zoom tiles are left out: the app rebuilds them."
        ),
    )
    parser.add_argument("--data", metavar="DIR", help="data folder (default: ~/Tabularium)")
    parser.add_argument("--out", metavar="FILE", help="archive to write (default: tabularium-backup-<time>.tar.gz in the current directory)")
    args = parser.parse_args(argv)
    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    import tarfile
    import tempfile
    from datetime import datetime
    from pathlib import Path

    from . import config
    from .services import backup

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out or f"tabularium-backup-{stamp}.tar.gz").expanduser().resolve()
    if out.is_relative_to(config.ROOT_DIR):
        print("tabularium backup: write the archive outside the data folder", file=sys.stderr)
        return 2
    if not config.ROOT_DIR.is_dir():
        print(f"tabularium backup: {config.ROOT_DIR} does not exist", file=sys.stderr)
        return 2

    def skipped(name: str) -> bool:
        return name in _BACKUP_SKIP_NAMES or name.endswith(_BACKUP_SKIP_SUFFIXES)

    root = config.ROOT_DIR
    # Installed, the .env lives in the data root: archive it once, as `.env`.
    env_file = config.ENV_FILE.resolve() if config.ENV_FILE.is_file() else None
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(out, "w:gz") as tar:
        if config.DB_PATH.exists():
            db_copy = Path(tmp) / "tabularium.db"
            if not backup.snapshot(db_copy)["ok"]:
                print("tabularium backup: the database copy failed its integrity check", file=sys.stderr)
                return 1
            tar.add(db_copy, arcname="data/tabularium.db")
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if here == root:
                dirnames[:] = [d for d in dirnames if not skipped(d)]
                filenames = [f for f in filenames if not skipped(f)]
            # os.walk does not descend into symlinked folders: keep them as links.
            entries = filenames + [d for d in dirnames if (here / d).is_symlink()]
            for name in entries:
                path = here / name
                if env_file is not None and path.resolve() == env_file:
                    continue
                try:
                    tar.add(path, arcname=f"data/{path.relative_to(root).as_posix()}", recursive=False)
                except FileNotFoundError:
                    continue  # removed while archiving: a running app's temporary file
        if env_file is not None:
            tar.add(env_file, arcname=".env")
    out.chmod(0o600)
    print(f"{out} ({out.stat().st_size / (1024 ** 2):.1f} MiB)")
    print("It contains the vault key (.env): keep it as private as the credentials it protects.")
    return 0


def _app_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2):  # noqa: S310
            return True
    except OSError:
        return False


def _restore_db(argv: list[str]) -> int:
    """`tabularium restore-db`: put a database backup back, offline, as it is."""
    parser = argparse.ArgumentParser(
        prog="tabularium restore-db",
        description=(
            "Replace the database with a backup, with Tabularium stopped. The current "
            "database is moved to <data>/pre-restore-<time>/, never deleted. The backup is "
            "not migrated: this is how you roll back to the release that wrote it."
        ),
    )
    parser.add_argument("backup", help="a file name in <data>/backups, or a path")
    parser.add_argument("--data", metavar="DIR", help="data folder (default: ~/Tabularium)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TABULARIUM_PORT", "8787")),
                        help="port of the running app, to refuse while it runs (default: 8787)")
    parser.add_argument("--yes", action="store_true", help="confirm the replacement")
    args = parser.parse_args(argv)
    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    import shutil
    from datetime import datetime
    from pathlib import Path

    from . import config
    from .services import backup

    source = Path(args.backup).expanduser()
    if not source.is_file():
        source = backup.backup_dir() / args.backup
    if not source.is_file():
        print(f"tabularium restore-db: {args.backup} not found", file=sys.stderr)
        return 2
    if not backup.integrity(source)["ok"]:
        print(f"tabularium restore-db: {source} fails the integrity check", file=sys.stderr)
        return 2
    if _app_running(args.port):
        print(f"tabularium restore-db: Tabularium answers on port {args.port}; stop it first", file=sys.stderr)
        return 2
    if not args.yes:
        print(f"This replaces {config.DB_PATH} with {source}.")
        print("Run again with --yes to confirm; the current database is kept aside.")
        return 2
    aside = config.ROOT_DIR / f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    aside.mkdir(parents=True, exist_ok=False)
    for suffix in ("", "-wal", "-shm"):
        current = config.DB_PATH.parent / (config.DB_PATH.name + suffix)
        if current.exists():
            shutil.move(str(current), aside / current.name)
    shutil.copy2(source, config.DB_PATH)
    print(f"Restored {source.name}; the previous database is in {aside}.")
    return 0


def _reset_password(argv: list[str]) -> int:
    """`tabularium reset-password`: a new password for a user locked out of the app."""
    parser = argparse.ArgumentParser(
        prog="tabularium reset-password",
        description=(
            "Set a new password for a user and close their open sessions, as an "
            "administrator would from the Users page. Without a username, list the users."
        ),
    )
    parser.add_argument("username", nargs="?", help="the user to reset")
    parser.add_argument("--password", help="the new password (default: ask, without echo)")
    parser.add_argument("--data", metavar="DIR", help="data folder (default: ~/Tabularium)")
    args = parser.parse_args(argv)
    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    import getpass

    from . import config
    from .db import connect
    from .services import users as usersvc

    if not config.DB_PATH.exists():
        print(f"tabularium reset-password: no database in {config.ROOT_DIR}; pass --data", file=sys.stderr)
        return 2
    with connect() as conn:
        rows = conn.execute("SELECT id, username, role, active FROM users ORDER BY id").fetchall()
        if not args.username:
            if not rows:
                print("No users yet: create the administrator on the first visit.")
            for row in rows:
                state = "active" if row["active"] else "inactive"
                print(f"{row['id']:>4}  {row['username']:<20} {row['role']:<8} {state}")
            return 0
        user = next((row for row in rows if row["username"] == args.username), None)
        if user is None:
            print(f"tabularium reset-password: no user named {args.username!r}", file=sys.stderr)
            return 1
        password = args.password
        if password is None:
            password = getpass.getpass(f"New password for {args.username}: ")
            if getpass.getpass("Repeat it: ") != password:
                print("tabularium reset-password: the two passwords differ", file=sys.stderr)
                return 1
        try:
            usersvc.set_password(conn, user["id"], password)
        except ValueError as exc:
            print(f"tabularium reset-password: {exc}", file=sys.stderr)
            return 1
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    print(f"Password of {args.username} changed; their open sessions are closed.")
    return 0


_COMMANDS = {
    "doctor": (_doctor, "check this installation without starting the app"),
    "models": (_models, "install a checkpoint offline, list the installed ones"),
    "backup": (_backup, "archive the data folder and the vault key"),
    "restore-db": (_restore_db, "put a database backup back, with the app stopped"),
    "reset-password": (_reset_password, "set a new password for a user"),
}


def _serving_on_a_terminal() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]][0](argv[1:])
    width = max(len(name) for name in _COMMANDS)
    parser = argparse.ArgumentParser(
        prog="tabularium",
        usage=(
            "tabularium [--host HOST] [--port PORT] [--data DIR] [--no-browser] [--version]\n"
            f"       tabularium {{{','.join(_COMMANDS)}}} [--help] ..."
        ),
        description="Start Tabularium and open it in the browser, or run one of the commands.",
        epilog="commands:\n" + "\n".join(
            f"  {name.ljust(width)}  {help_text}" for name, (_, help_text) in _COMMANDS.items()
        ) + "\n\nRun `tabularium <command> --help` for the options of each command.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=os.environ.get("TABULARIUM_HOST", "127.0.0.1"),
                        help="address to bind (default: 127.0.0.1, this computer only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TABULARIUM_PORT", "8787")),
                        help="port to listen on (default: 8787)")
    parser.add_argument("--data", metavar="DIR",
                        help="data folder (default: ~/Tabularium, or TABULARIUM_ROOT)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    # One process is a design constraint, not a default: downloads, jobs and the live
    # channel keep state in memory (program.md D8). Refuse before it breaks silently.
    parser.add_argument(
        "--workers", type=int, default=1,
        help="must be 1: the application keeps job and presence state in process",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(argv)

    # La radice dati va fissata prima di importare la configurazione, che la
    # legge all'import (anche per trovare il `.env`).
    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    from . import config

    if args.version:
        print(f"Tabularium {config.VERSION}")
        return 0

    if args.workers != 1:
        print(
            "tabularium: --workers must be 1. Downloads, training jobs and live presence "
            "live in the process; more workers would duplicate them and split the sessions.",
            file=sys.stderr,
        )
        return 2

    problem = config.python_problem()
    if problem:
        print(f"tabularium: warning: {problem}. Reinstall with: {config.PYTHON_REINSTALL}", file=sys.stderr)

    import uvicorn

    shown_host = "localhost" if args.host in {"127.0.0.1", "0.0.0.0", "::"} else args.host
    url = f"http://{shown_host}:{args.port}"
    public_url = os.environ.get("TABULARIUM_PUBLIC_URL", "").strip().rstrip("/")
    print(f"Tabularium {config.VERSION}")
    print(f"  data:      {config.ROOT_DIR}")
    if public_url:
        print(f"  interface: {public_url}")
        print(f"  listening: {args.host}:{args.port}")
    else:
        print(f"  interface: {url}")
    # Under systemd or in a container there is no terminal to press Ctrl+C in.
    if _serving_on_a_terminal():
        print("  stop:      Ctrl+C")
    sys.stdout.flush()
    if not args.no_browser:
        from .services import setup_token

        # The first visit creates the administrator: the browser we open carries the token.
        token = setup_token.pending()
        target = f"{url}/?setup_token={token}" if token else url
        threading.Thread(target=_open_when_ready, args=(url, target), daemon=True).start()
    uvicorn.run(
        "app.main:app", host=args.host, port=args.port, workers=1,
        proxy_headers=True, forwarded_allow_ips=config.FORWARDED_ALLOW_IPS,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
