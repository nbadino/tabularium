"""Comando `tabularium`: avvia l'app installata come pacchetto.

È l'ingresso di `uv tool install tabularium` / `pipx install tabularium`:
un solo processo serve API e interfaccia, come `scripts/run.sh` da un
checkout, e quando il server risponde apre il browser.
"""
from __future__ import annotations

import argparse
import os
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tabularium",
        description="Start Tabularium and open it in the browser.",
    )
    parser.add_argument("--host", default=os.environ.get("TABULARIUM_HOST", "127.0.0.1"),
                        help="address to bind (default: 127.0.0.1, this computer only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TABULARIUM_PORT", "8787")),
                        help="port to listen on (default: 8787)")
    parser.add_argument("--data", metavar="DIR",
                        help="data folder (default: ~/Tabularium, or TABULARIUM_ROOT)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
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

    import uvicorn

    shown_host = "localhost" if args.host in {"127.0.0.1", "0.0.0.0", "::"} else args.host
    url = f"http://{shown_host}:{args.port}"
    print(f"Tabularium {config.VERSION}")
    print(f"  data:      {config.ROOT_DIR}")
    print(f"  interface: {url}")
    print("  stop:      Ctrl+C", flush=True)
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run("app.main:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
