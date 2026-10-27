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


def _open_when_ready(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=2):  # noqa: S310
                webbrowser.open(url)
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "doctor":
        return _doctor(argv[1:])
    if argv and argv[0] == "models":
        return _models(argv[1:])
    parser = argparse.ArgumentParser(
        prog="tabularium",
        description="Start Tabularium and open it in the browser.",
        epilog=(
            "Run `tabularium doctor` to check an installation without starting it, and "
            "`tabularium models import <dir> --adapter <id>` to install a checkpoint offline."
        ),
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

    import uvicorn

    shown_host = "localhost" if args.host in {"127.0.0.1", "0.0.0.0", "::"} else args.host
    url = f"http://{shown_host}:{args.port}"
    print(f"Tabularium {config.VERSION}")
    print(f"  data:      {config.ROOT_DIR}")
    print(f"  interface: {url}")
    print("  stop:      Ctrl+C", flush=True)
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run("app.main:app", host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
