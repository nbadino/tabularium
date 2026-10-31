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


def check_data_dir() -> Check:
    root = config.ROOT_DIR
    if not root.exists():
        return _check(
            "data directory", WARN, f"{root} non esiste ancora",
            "verrà creata al primo avvio; se il genitore non è scrivibile, l'avvio fallirà",
        )
    if not _writable(root):
        return _check(
            "data directory", FAIL, f"{root} non è scrivibile",
            "concedi i permessi all'utente che esegue il processo, non a root",
        )
    return _check("data directory", OK, f"{root} (scrivibile)")


def check_disk() -> Check:
    try:
        usage = shutil.disk_usage(config.ROOT_DIR if config.ROOT_DIR.exists() else config.ROOT_DIR.parent)
    except OSError as exc:
        return _check("free space", WARN, f"non misurabile: {exc}")
    free_gb = usage.free / (1024 ** 3)
    detail = f"{free_gb:.1f} GiB liberi su {config.ROOT_DIR}"
    if free_gb < 2:
        return _check("free space", FAIL, detail, "servono almeno 2 GiB per un progetto e i crop")
    if free_gb < 20:
        return _check("free space", WARN, detail, "un checkpoint addestrato occupa diversi GiB")
    return _check("free space", OK, detail)


def check_database() -> Check:
    path = config.DB_PATH
    if not path.exists():
        return _check(
            "database", WARN, f"{path} non esiste",
            "viene creato al primo avvio con le migrazioni",
        )
    try:
        from . import backup

        report = backup.integrity(path)
    except Exception as exc:  # noqa: BLE001 - un DB illeggibile è un risultato, non un crash
        return _check("database", FAIL, f"non leggibile: {exc}", "controlla permessi e disco")
    if not report["ok"]:
        return _check(
            "database", FAIL, "integrity_check non superato: " + "; ".join(report["messages"])[:200],
            "ripristina l'ultimo backup da <data>/backups",
        )
    return _check("database", OK, f"{path} ({report['journal_mode']})")


def check_schema() -> Check:
    path = config.DB_PATH
    if not path.exists():
        return _check("schema", WARN, "nessun database da verificare")
    try:
        from ..adapters import store

        current = store.get("schema_version", "assente")
    except Exception as exc:  # noqa: BLE001
        return _check("schema", FAIL, f"versione non leggibile: {exc}")
    from ..db import SCHEMA_VERSION

    if str(current).isdigit() and int(current) > int(SCHEMA_VERSION):
        return _check(
            "schema", FAIL, f"{current}, newer than this release ({SCHEMA_VERSION})",
            "a later release upgraded this database: start it again, or restore the "
            "pre-upgrade backup with `tabularium restore-db`",
        )
    if str(current) != str(SCHEMA_VERSION):
        return _check(
            "schema", FAIL, f"{current} invece di {SCHEMA_VERSION}",
            "avvia il backend una volta: applica le migrazioni; se resta indietro, la catena è incompleta",
        )
    return _check("schema", OK, f"versione {current}")


def check_port(host: str, port: int) -> Check:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host if host not in {"0.0.0.0", "::"} else "", port))
    except OSError as exc:
        return _check(
            "port", FAIL, f"{host}:{port} non disponibile ({exc.strerror or exc})",
            "ferma l'altro processo o usa --port",
        )
    finally:
        probe.close()
    return _check("port", OK, f"{host}:{port} libera")


def check_auth() -> Check:
    if not config.auth_enabled():
        return _check(
            "authentication", WARN, "disattivata (TABULARIUM_AUTH=off)",
            "va bene solo su una macchina personale: chiunque raggiunga la porta vede i progetti",
        )
    path = config.DB_PATH
    if not path.exists():
        return _check("authentication", OK, "attiva; nessun utente ancora (setup al primo avvio)")
    try:
        from ..adapters import users as userstore

        admins = userstore.count_active_admins()
        users = userstore.count_users()
    except Exception as exc:  # noqa: BLE001
        return _check("authentication", FAIL, f"utenti non leggibili: {exc}")
    if admins == 0:
        return _check(
            "authentication", FAIL, "nessun amministratore attivo",
            "crea l'amministratore dal setup al primo avvio; se l'istanza è già inizializzata, usa scripts/reset_password.py",
        )
    return _check("authentication", OK, f"{users} utenti, {admins} amministratori")


def check_vault() -> Check:
    if not config.DB_PATH.exists():
        # No database means no credentials to decrypt yet: the key is generated on the
        # first start, so this is not a failure of the installation.
        return _check("credential vault", WARN, "nessun database: la chiave nasce al primo avvio")
    try:
        from . import vault

        problem = vault.key_problem()
    except Exception as exc:  # noqa: BLE001 - an unreadable vault is a result, not a crash
        return _check("credential vault", FAIL, f"chiave non verificabile: {exc}")
    if problem:
        return _check(
            "credential vault", FAIL, problem,
            "reinserisci le chiavi dei provider dalle Impostazioni: i valori salvati con la vecchia chiave non sono leggibili",
        )
    return _check("credential vault", OK, "chiave disponibile")


def check_frontend() -> Check:
    dist = config.FRONTEND_DIST
    index = dist / "index.html"
    if not index.exists():
        return _check(
            "interface", FAIL, f"manca {index}",
            "nella wheel l'interfaccia è inclusa: se manca, l'installazione è incompleta",
        )
    return _check("interface", OK, str(dist))


def check_web_concurrency() -> Check:
    raw = os.environ.get("WEB_CONCURRENCY", "").strip()
    if raw and raw.isdigit() and int(raw) > 1:
        return _check(
            "process model", FAIL, f"WEB_CONCURRENCY={raw}",
            "Tabularium è un'appliance a processo singolo: download, job e presenza vivono in memoria",
        )
    return _check("process model", OK, "processo singolo")


def check_gpu() -> Check:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return _check(
            "GPU", WARN, "nvidia-smi non trovato",
            "riconoscimento e training girano su CPU o su un provider remoto; per il locale serve CUDA",
        )
    try:
        out = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _check("GPU", WARN, f"nvidia-smi non eseguibile: {exc}")
    names = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    if out.returncode != 0 or not names:
        return _check("GPU", WARN, "nessuna GPU visibile")
    return _check("GPU", OK, "; ".join(names[:4]))


def check_runtimes() -> Check:
    found = sorted(path.name for path in config.ROOT_DIR.glob("*-runtime") if path.is_dir())
    if not found:
        return _check(
            "model runtimes", WARN, "nessun runtime locale preparato",
            "si creano dal registro modelli al primo uso; non servono per un provider remoto",
        )
    return _check("model runtimes", OK, ", ".join(found))


def check_huggingface() -> Check:
    base = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    try:
        with urllib.request.urlopen(base, timeout=3):  # noqa: S310 - endpoint configurabile
            return _check("model downloads", OK, f"{base} raggiungibile")
    except (urllib.error.URLError, OSError) as exc:
        return _check(
            "model downloads", WARN, f"{base} non raggiungibile ({exc})",
            "va bene se i modelli sono già installati o importati offline",
        )


def run(host: str = "127.0.0.1", port: int = 8787) -> list[Check]:
    return [
        check_data_dir(),
        check_disk(),
        check_database(),
        check_schema(),
        check_port(host, port),
        check_auth(),
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
    lines = [f"Tabularium {version} — doctor", ""]
    for check in checks:
        lines.append(f"{marks[check.status]} {check.name.ljust(width)}  {check.detail}")
        if check.hint and check.status != OK:
            lines.append(f"       {' ' * width}  → {check.hint}")
    counts = {status: sum(1 for check in checks if check.status == status) for status in (OK, WARN, FAIL)}
    lines += [
        "",
        f"{len(checks)} controlli: {counts[OK]} ok, {counts[WARN]} avvisi, {counts[FAIL]} errori",
    ]
    if counts[FAIL]:
        lines.append("Correggi gli errori prima di considerare l'installazione riuscita.")
    return "\n".join(lines)
