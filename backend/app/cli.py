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


def _recognition(argv: list[str]) -> int:
    """`tabularium recognition`: store results a model produced on another machine."""
    parser = argparse.ArgumentParser(
        prog="tabularium recognition",
        description="Recognition work done outside the running application.",
    )
    parser.add_argument("--data", metavar="DIR", help="data folder (default: ~/Tabularium)")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser(
        "import",
        help="store results read elsewhere as drafts, through a recognition run",
        description=(
            "Read one <page id>.json per page from a folder (what the model's gateway returns "
            "for the page) and write it as the application would after reading the page itself: "
            "unverified drafts are replaced, what a person drew or verified stays. Only pages in "
            "the chosen statuses are touched. Run it with --dry-run first."
        ),
    )
    importer.add_argument("source", help="folder with one <page id>.json per page")
    importer.add_argument("--project", type=int, required=True, help="project id")
    importer.add_argument("--adapter", required=True, help="adapter id of the model that read the pages")
    importer.add_argument(
        "--status", action="append", metavar="STATUS",
        help="page status to import into; repeat for more (default: new)",
    )
    importer.add_argument("--limit", type=int, help="import at most this many pages (lowest ids first)")
    importer.add_argument("--chunk", type=int, default=None, help="pages per recognition run")
    importer.add_argument("--dry-run", action="store_true", help="say what would be imported, write nothing")
    importer.add_argument(
        "--again", action="store_true",
        help="also write the pages an earlier import of this model already wrote",
    )
    second = commands.add_parser(
        "import-second-reading",
        help="store the cells a second reading doubts as a comparison between two models",
        description=(
            "Read one <page id>.json per page from a folder: the model's result for the page "
            "plus `doubts`, the table cells a second recognizer read differently. Each page "
            "with doubts gets a finished comparison (first reading against second reading), "
            "which the Studio shows as cells to check. No page is written. Run it with "
            "--dry-run first."
        ),
    )
    second.add_argument("source", help="folder with one <page id>.json per page")
    second.add_argument("--project", type=int, required=True, help="project id")
    second.add_argument("--adapter", required=True, help="adapter id of the model that read the pages")
    second.add_argument("--name", required=True, help="how the second reading is called in the comparison")
    second.add_argument(
        "--status", action="append", metavar="STATUS",
        help="page status to import for; repeat for more (default: new)",
    )
    second.add_argument("--limit", type=int, help="import at most this many pages (lowest ids first)")
    second.add_argument("--dry-run", action="store_true", help="say what would be imported, write nothing")
    second.add_argument(
        "--again", action="store_true",
        help="also import the pages an earlier import of a second reading already covers",
    )

    args = parser.parse_args(argv)
    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    from .db import init_db
    from .services import recognition

    init_db()
    if args.command == "import-second-reading":
        return _import_second_reading(args)
    done = {"pages": 0, "failed": 0}

    def progress(page_id: int, failure: str | None) -> None:
        done["pages"] += 1
        if failure:
            done["failed"] += 1
            print(f"  page {page_id}: {failure}", file=sys.stderr)
        if done["pages"] % 250 == 0:
            print(f"  {done['pages']} pages, {done['failed']} failed", flush=True)

    try:
        report = recognition.import_results(
            args.project, args.source, adapter_id=args.adapter,
            statuses=tuple(args.status or ("new",)), limit=args.limit,
            chunk=args.chunk or recognition.IMPORT_CHUNK, dry_run=args.dry_run, again=args.again,
            progress=progress,
        )
    except ValueError as exc:
        print(f"tabularium recognition import: {exc}", file=sys.stderr)
        return 2
    skipped = report["skipped"]
    print(f"{report['files']} result files in {args.source}")
    if skipped["not_in_project"]:
        print(f"  {skipped['not_in_project']} belong to no page of project {args.project}")
    for status, count in sorted(skipped["status"].items()):
        print(f"  {count} pages left alone: status {status}")
    if skipped["already_imported"]:
        print(f"  {skipped['already_imported']} pages left alone: already imported (--again writes them once more)")
    if skipped["transformed"]:
        print(f"  {skipped['transformed']} pages left alone: straightened, the boxes would not fit")
    if report["dry_run"]:
        print(f"{report['selected']} pages would be imported; nothing was written")
        return 0
    runs = ", ".join(f"#{run_id}" for run_id in report["runs"]) or "none"
    print(f"imported {report['imported']} pages, {report['failed']} failed (recognition runs: {runs})")
    return 1 if report["failed"] else 0


def _import_second_reading(args) -> int:
    from .services import consensus

    done = {"pages": 0, "failed": 0}

    def progress(page_id: int, failure: str | None) -> None:
        done["pages"] += 1
        if failure:
            done["failed"] += 1
            print(f"  page {page_id}: {failure}", file=sys.stderr)
        if done["pages"] % 250 == 0:
            print(f"  {done['pages']} pages, {done['failed']} failed", flush=True)

    try:
        report = consensus.import_second_reading(
            args.project, args.source, adapter_id=args.adapter, second_name=args.name,
            statuses=tuple(args.status or ("new",)), limit=args.limit,
            dry_run=args.dry_run, again=args.again, progress=progress,
        )
    except ValueError as exc:
        print(f"tabularium recognition import-second-reading: {exc}", file=sys.stderr)
        return 2
    skipped = report["skipped"]
    print(f"{report['files']} result files in {args.source}")
    if skipped["not_in_project"]:
        print(f"  {skipped['not_in_project']} belong to no page of project {args.project}")
    for status, count in sorted(skipped["status"].items()):
        print(f"  {count} pages left alone: status {status}")
    for key, why in (
        ("transformed", "straightened, the boxes would not fit"),
        ("already_imported", "second reading already imported (--again imports it once more)"),
        ("compared", "a comparison between other models is there"),
        ("no_doubts", "the second reading doubts no cell"),
    ):
        if skipped[key]:
            print(f"  {skipped[key]} pages left alone: {why}")
    if report["dry_run"]:
        print(f"{report['selected']} pages would get a comparison; nothing was written")
        return 0
    runs = ", ".join(f"#{run_id}" for run_id in report["runs"]) or "none"
    print(
        f"imported {report['imported']} pages with {report['doubts']} doubted cells, "
        f"{report['failed']} failed (comparisons: {runs})"
    )
    return 1 if report["failed"] else 0


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
    parser.add_argument("--runs", metavar="ID[,ID...]",
                        help="keep the checkpoints of these training runs only; the other runs keep "
                             "their logs and metrics without weights (default: every run)")
    args = parser.parse_args(argv)
    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    import sqlite3
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
    keep_runs = None
    if args.runs is not None:
        keep_runs = {run.strip() for run in args.runs.split(",") if run.strip()}
        # A typo would write an archive without the model the user wanted to move.
        unknown = keep_runs - {path.name for path in config.DATA_DIR.glob("*/runs/*") if path.is_dir()}
        if unknown:
            print(f"tabularium backup: no training run named {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
    outside: list[str] = []
    # Installed, the .env lives in the data root: archive it once, as `.env`.
    env_file = config.ENV_FILE.resolve() if config.ENV_FILE.is_file() else None
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(out, "w:gz") as tar:
        if config.DB_PATH.exists():
            db_copy = Path(tmp) / "tabularium.db"
            if not backup.snapshot(db_copy)["ok"]:
                print("tabularium backup: the database copy failed its integrity check", file=sys.stderr)
                return 1
            tar.add(db_copy, arcname="data/tabularium.db")
            # Pages registered in place keep their images where they are: say so.
            prefix = str(root).rstrip("/\\") + os.sep
            with sqlite3.connect(db_copy) as conn:
                rows = conn.execute("SELECT abs_path FROM pages UNION SELECT archive_dir FROM projects").fetchall()
            outside = sorted({row[0] for row in rows if row[0] and not row[0].startswith(prefix)})
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if here == root:
                dirnames[:] = [d for d in dirnames if not skipped(d)]
                filenames = [f for f in filenames if not skipped(f)]
            elif (keep_runs is not None and here.parent.name == "runs"
                  and here.parent.parent.parent == config.DATA_DIR and here.name not in keep_runs):
                dirnames[:] = [d for d in dirnames if d != "checkpoints"]
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
    if outside:
        print(f"warning: {len(outside)} paths are outside {root} and not in the archive; "
              f"copy them yourself to the same place, e.g. {outside[0]}", file=sys.stderr)
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


def _extract_backup(archive, dest) -> list[str]:
    """Extract a `tabularium backup` archive into `dest`; return the links left out."""
    import tarfile

    left_out = []
    with tarfile.open(archive) as tar:
        for member in tar:
            if member.name != ".env" and not member.name.startswith("data/"):
                raise ValueError(f"{member.name!r} is not part of a Tabularium backup")
            try:
                # `data` refuses absolute names, `..` and device files (CVE-2007-4559).
                tar.extract(member, dest, filter="data")
            except (tarfile.AbsoluteLinkError, tarfile.LinkOutsideDestinationError):
                left_out.append(member.name)  # a folder linked from elsewhere on the other machine
    return left_out


def _restore(argv: list[str]) -> int:
    """`tabularium restore`: put a `tabularium backup` archive back, offline."""
    parser = argparse.ArgumentParser(
        prog="tabularium restore",
        description=(
            "Restore an archive written by `tabularium backup`, with Tabularium stopped. "
            "--replace swaps the whole data folder: the current one is moved next to it, "
            "to <data>.pre-restore-<time>, never deleted, and this computer's model runtimes "
            "and caches are carried over. The archive's vault key replaces the one in .env, "
            "whose previous copy is kept aside. Stored paths follow the new folder."
        ),
    )
    parser.add_argument("archive", help="a .tar.gz written by `tabularium backup`")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replace", action="store_true",
                      help="replace every project here with the archive's")
    parser.add_argument("--data", metavar="DIR", help="data folder (default: ~/Tabularium)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TABULARIUM_PORT", "8787")),
                        help="port of the running app, to refuse while it runs (default: 8787)")
    parser.add_argument("--yes", action="store_true", help="confirm the replacement")
    args = parser.parse_args(argv)
    if args.data:
        os.environ["TABULARIUM_ROOT"] = os.path.abspath(os.path.expanduser(args.data))

    import shutil
    import sqlite3
    import tarfile
    from datetime import datetime
    from pathlib import Path

    from . import config, db
    from .services import backup

    archive = Path(args.archive).expanduser().resolve()
    if not archive.is_file():
        print(f"tabularium restore: {args.archive} not found", file=sys.stderr)
        return 2
    if _app_running(args.port):
        print(f"tabularium restore: Tabularium answers on port {args.port}; stop it first", file=sys.stderr)
        return 2
    root = config.ROOT_DIR
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    aside = root.parent / f"{root.name}.pre-restore-{stamp}"
    if not args.yes:
        print(f"This replaces the data folder {root} with the one in {archive.name}.")
        print(f"The current folder is moved to {aside}. Run again with --yes to confirm.")
        return 2

    # Same parent as the data folder: the final swap is a rename, not a copy.
    staging = root.parent / f".{root.name}.restore-{stamp}"
    staging.mkdir(parents=True)
    try:
        left_out = _extract_backup(archive, staging)
        restored_db = staging / "data" / "tabularium.db"
        if not restored_db.is_file():
            raise ValueError("the archive has no data/tabularium.db")
        if not backup.integrity(restored_db)["ok"]:
            raise ValueError("the archive's database fails the integrity check")
        with sqlite3.connect(restored_db) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row and str(row[0]).isdigit() and int(row[0]) > int(db.SCHEMA_VERSION):
            raise ValueError("the archive comes from a newer release: upgrade Tabularium first")
        env_file = staging / ".env"
        vault_lines = [line for line in env_file.read_text().splitlines()
                       if line.startswith("TABULARIUM_VAULT_KEY=")] if env_file.is_file() else []
    except (ValueError, OSError, tarfile.TarError) as exc:
        shutil.rmtree(staging, ignore_errors=True)  # only the copy extracted above
        print(f"tabularium restore: {exc}", file=sys.stderr)
        return 2

    env_inside = config.ENV_FILE.resolve().is_relative_to(root.resolve()) if root.exists() else False
    if root.exists():
        root.rename(aside)
    else:
        aside.mkdir(parents=True)
    (staging / "data").rename(root)
    for child in aside.iterdir():
        # Rebuildable and specific to this computer: the archive never carries them.
        if child.name in ("vendor", "cache") or child.name.endswith(_BACKUP_SKIP_SUFFIXES):
            if not (root / child.name).exists():
                child.rename(root / child.name)
    if vault_lines:
        current = aside / config.ENV_FILE.name if env_inside else config.ENV_FILE
        lines = current.read_text().splitlines() if current.is_file() else []
        if current.is_file() and not env_inside:
            shutil.copy2(current, aside / "env.pre-restore")
        lines = [line for line in lines if not line.startswith("TABULARIUM_VAULT_KEY=")] + vault_lines[-1:]
        config.ENV_FILE.write_text("\n".join(lines) + "\n")
        config.ENV_FILE.chmod(0o600)
    shutil.rmtree(staging, ignore_errors=True)  # holds at most the archive's .env

    db.init_db()  # migrates, and rewrites the paths of the other machine's data folder
    with db.connect() as conn:
        pages = [row[0] for row in conn.execute("SELECT abs_path FROM pages WHERE abs_path IS NOT NULL")]
    missing = sum(1 for path in pages if not Path(path).is_file())
    print(f"Restored {archive.name} into {root}; the previous folder is in {aside}.")
    if not vault_lines:
        print("warning: the archive has no vault key; credentials saved in the UI must be entered again", file=sys.stderr)
    if missing:
        print(f"warning: {missing} of {len(pages)} pages have no image at their stored path", file=sys.stderr)
    if left_out:
        print(f"warning: {len(left_out)} links to folders outside the data folder were left out", file=sys.stderr)
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
    "recognition": (_recognition, "import results a model produced on another machine"),
    "backup": (_backup, "archive the data folder and the vault key"),
    "restore": (_restore, "restore a backup archive, with the app stopped"),
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
