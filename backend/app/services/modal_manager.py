"""Gestione Modal Serverless dal terminale dell'app (senza CLI utente).

Modal è il metodo «pay-per-second»: la GPU si accende a ogni richiesta e si
spegne dopo il periodo caldo. I due gesti che di norma richiedono il
terminale — autenticazione (`modal token new`) e deploy della template
(`modal deploy`) — qui sono orchestrati dal backend come sottoprocessi, con
stato e log interrogabili dalla UI:

- il token si ottiene con `modal token new`: su una macchina locale il CLI
  apre il browser dell'utente, che approva; il processo termina da solo;
- il deploy costruisce l'immagine e scarica i pesi alla prima esecuzione
  (minuti), poi stampa l'URL dell'endpoint che la UI propone per la card
  di inferenza.

Il task manager è deliberatamente semplice (un task alla volta, stato in
memoria): è infrastruttura occasionale, non un sistema di code.
"""
from __future__ import annotations

import json
import os
import sqlite3
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..db import connect

REPO_ROOT = Path(__file__).resolve().parents[3]

# Pattern dell'endpoint stampato da `modal deploy`.
URL_PREFIX = "https://"
URL_SUFFIX = ".modal.run"


@dataclass(frozen=True)
class ModalTemplate:
    """Una template di deploy serverless: script + nome app Modal (da cui
    l'URL dell'endpoint è deterministico: workspace + nome app)."""

    id: str
    label: str
    app_name: str
    script: Path


TEMPLATES: dict[str, ModalTemplate] = {
    t.id: t
    for t in (
        ModalTemplate(
            id="monkeyocrv2",
            label="MonkeyOCRv2-Parsing",
            app_name="tabularium-vllm",
            script=REPO_ROOT / "scripts" / "cloud" / "modal_vllm.py",
        ),
        ModalTemplate(
            id="paddleocr-vl",
            label="PaddleOCR-VL-1.6",
            app_name="tabularium-paddleocr-vl",
            script=REPO_ROOT / "scripts" / "cloud" / "modal_paddleocr_vl.py",
        ),
        ModalTemplate(
            id="mineru",
            label="MinerU2.5",
            app_name="tabularium-mineru",
            script=REPO_ROOT / "scripts" / "cloud" / "modal_mineru.py",
        ),
        ModalTemplate(
            id="unlimited-ocr",
            label="Unlimited-OCR",
            app_name="tabularium-unlimited-ocr",
            script=REPO_ROOT / "scripts" / "cloud" / "modal_unlimited_ocr.py",
        ),
        ModalTemplate(
            id="dots-ocr",
            label="dots.mocr",
            app_name="tabularium-dots-ocr",
            script=REPO_ROOT / "scripts" / "cloud" / "modal_dots_ocr.py",
        ),
        ModalTemplate(
            id="glm-ocr",
            label="GLM-OCR",
            app_name="tabularium-glm-ocr",
            script=REPO_ROOT / "scripts" / "cloud" / "modal_glm_ocr.py",
        ),
        ModalTemplate(
            id="deepseek-ocr",
            label="DeepSeek-OCR-2",
            app_name="tabularium-deepseek-ocr",
            script=REPO_ROOT / "scripts" / "cloud" / "modal_deepseek_ocr.py",
        ),
        ModalTemplate(
            id="qwen3-vl",
            label="Qwen3-VL-8B",
            app_name="tabularium-qwen3-vl",
            script=REPO_ROOT / "scripts" / "cloud" / "modal_qwen3_vl.py",
        ),
    )
}
DEFAULT_TEMPLATE = "monkeyocrv2"


def list_templates() -> list[dict]:
    return [{"id": t.id, "label": t.label} for t in TEMPLATES.values()]


def _template(template_id: str | None) -> ModalTemplate:
    tid = template_id or DEFAULT_TEMPLATE
    try:
        return TEMPLATES[tid]
    except KeyError as exc:
        raise ValueError(f"template Modal sconosciuta: {tid}") from exc


@dataclass
class ModalTask:
    """Un sottoprocesso modal in corso (setup o deploy)."""

    kind: str  # 'setup' | 'deploy'
    proc: subprocess.Popen
    # None per 'setup' (l'autenticazione non è legata a una template).
    template_id: str | None = None
    log: list[str] = field(default_factory=list)
    done: bool = False
    ok: bool | None = None
    log_path: Path | None = None
    job_id: int | None = None

    def drain(self) -> None:
        """Consuma l'output disponibile senza bloccare (log live in UI)."""
        if self.log_path is not None:
            try:
                self.log = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
            except OSError:
                pass


_task: ModalTask | None = None
_task_lock = threading.Lock()


def _find_modal() -> str | None:
    """Il CLI modal, in ordine: accanto al Python del backend, nel venv base
    del repo (run.sh può usare il venv UVDoc dedicato, che non contiene la
    CLI — essa è indipendente e vive nel venv base), poi nel PATH.

    Niente ``resolve()``: il python del venv è un symlink al sistema e
    risolverlo manderebbe la ricerca in ``/usr/bin``.
    """
    candidates = [
        Path(sys.executable).parent / "modal",
        REPO_ROOT / "backend" / ".venv" / "bin" / "modal",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("modal")


def _watch(task: ModalTask) -> None:
    """Thread di supervisione: drena il log e chiude il task alla fine."""
    import time

    global _token_cache
    while task.proc.poll() is None:
        task.drain()
        time.sleep(0.3)
    task.drain()
    task.done = True
    task.ok = task.proc.returncode == 0
    if task.job_id is not None:
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE jobs SET state=?, ended_at=datetime('now'), exit_code=?, heartbeat_at=datetime('now') WHERE id=?",
                    ("finished" if task.ok else "failed", task.proc.returncode, task.job_id),
                )
        except sqlite3.OperationalError:
            pass
    _token_cache = None  # il setup/deploy può aver cambiato lo stato del token
    _invalidate_cache()  # idem per lo stato dell'app


def _persisted_running() -> dict | None:
    """Recupera il minimo stato osservabile di un task sopravvissuto al restart."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE kind='modal' AND state='running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    pid = int(row["pid"] or 0)
    try:
        if pid <= 0:
            raise OSError("PID Modal assente")
        os.kill(pid, 0)
    except OSError:
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE jobs SET state='failed', ended_at=datetime('now'), error=?, heartbeat_at=datetime('now') WHERE id=?",
                    ("processo Modal non più presente dopo il riavvio", row["id"]),
                )
        except sqlite3.OperationalError:
            pass
        return None
    try:
        command = json.loads(row["command_json"] or "{}")
    except (TypeError, ValueError):
        command = {}
    return {
        "id": row["id"],
        "owner_id": row["owner_id"],
        "pid": pid,
        "kind": command.get("kind", "deploy"),
        "template_id": command.get("template_id"),
        "log_path": row["log_path"],
    }


def _start(
    kind: str,
    args: list[str],
    env: dict[str, str] | None = None,
    template_id: str | None = None,
    owner_id: int | None = None,
) -> None:
    global _task
    with _task_lock:
        if _task is not None and not _task.done:
            raise RuntimeError("un task Modal è già in corso")
        if _persisted_running() is not None:
            raise RuntimeError("un task Modal è già in corso")
        exe = _find_modal()
        if exe is None:
            raise RuntimeError(
                "CLI modal non trovata: installarla con "
                "`backend/.venv/bin/pip install modal` e riavviare Tabularium"
            )
        log_path = config.ROOT_DIR / "modal" / f"{kind}-{int(time.time() * 1000)}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(  # noqa: S603
            [exe, *args],
            cwd=str(REPO_ROOT),
            stdout=log_path.open("a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        try:
            with connect() as conn:
                cur = conn.execute(
                    "INSERT INTO jobs(kind, owner_id, provider, pid, process_group, state, heartbeat_at, command_json, log_path, recovery_strategy) "
                    "VALUES('modal', ?, 'modal', ?, ?, 'running', datetime('now'), ?, ?, 'pid-process-group')",
                    (owner_id, proc.pid, os.getpgid(proc.pid) if hasattr(os, "getpgid") else proc.pid,
                     json.dumps({"kind": kind, "template_id": template_id, "args": args}), str(log_path)),
                )
                job_id = int(cur.lastrowid)
        except sqlite3.OperationalError as exc:
            proc.terminate()
            raise RuntimeError("tabella jobs non disponibile: eseguire init_db()") from exc
        _task = ModalTask(kind=kind, proc=proc, template_id=template_id, log_path=log_path, job_id=job_id)
        threading.Thread(target=_watch, args=(_task,), daemon=True).start()


def is_deploying() -> bool:
    task = _current()
    if task is not None and task.kind == "deploy" and not task.done:
        return True
    persisted = _persisted_running()
    return bool(persisted and persisted.get("kind") == "deploy")


def current_kind() -> str | None:
    task = _current()
    if task is not None and not task.done:
        return task.kind
    persisted = _persisted_running()
    return persisted.get("kind") if persisted else None


def _current() -> ModalTask | None:
    return _task if _task is not None and not _task.done else None


_token_cache: tuple[bool, float] | None = None
_TOKEN_TTL = 30.0


def token_configured() -> bool:
    """Vero se esiste già un token Modal valido sul disco (check con cache)."""
    global _token_cache
    import time  # noqa: PLC0415

    if _token_cache is not None and time.time() - _token_cache[1] < _TOKEN_TTL:
        return _token_cache[0]
    exe = _find_modal()
    if exe is None:
        return False
    try:
        res = subprocess.run(  # noqa: S603
            [exe, "token", "info"], capture_output=True, text=True, timeout=30
        )
        ok = res.returncode == 0
    except Exception:  # noqa: BLE001
        ok = False
    _token_cache = (ok, time.time())
    return ok


def start_setup(owner_id: int | None = None) -> None:
    """Autenticazione: apre il browser per approvare il token."""
    _start("setup", ["token", "new"], owner_id=owner_id)


def start_deploy(
    template_id: str | None = None,
    api_key: str | None = None,
    keep_warm: bool = False,
    owner_id: int | None = None,
) -> None:
    """Deploy della template serverless (costruzione immagine + endpoint).

    Lo scale-to-zero è il default sicuro: ``min_containers=1`` addebita la GPU
    anche quando nessuno sta usando l'app. Il warm container va scelto
    esplicitamente dalla UI per una sessione di lavoro attiva.
    """
    template = _template(template_id)
    env = dict(os.environ)
    if api_key:
        env["TABULARIUM_VLLM_API_KEY"] = api_key
    env["TABULARIUM_MODAL_MIN_CONTAINERS"] = "1" if keep_warm else "0"
    _start("deploy", ["deploy", str(template.script)], env=env, template_id=template.id, owner_id=owner_id)


def stop_app(template_id: str | None = None, owner_id: int | None = None) -> None:
    """Ferma l'app Modal e termina i container attivi."""
    template = _template(template_id)
    exe = _find_modal()
    if exe is None:
        raise RuntimeError("CLI Modal non trovata")
    _cancel_deploy_if_running(template.id)
    _start(
        "stop",
        ["app", "stop", "-y", template.app_name],
        env=dict(os.environ),
        template_id=template.id,
        owner_id=owner_id,
    )


def _cancel_deploy_if_running(template_id: str) -> None:
    """Interrompe un deploy recuperato prima di inviare `modal app stop`."""
    global _task
    task = _task
    if task is not None and not task.done and task.kind == "deploy" and task.template_id == template_id:
        task.proc.terminate()
        try:
            task.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            task.proc.kill()
        if task.job_id is not None:
            try:
                with connect() as conn:
                    conn.execute(
                        "UPDATE jobs SET state='stopped', ended_at=datetime('now'), heartbeat_at=datetime('now'), error=? WHERE id=?",
                        ("deploy interrotto prima dello stop dell’app", task.job_id),
                    )
            except sqlite3.OperationalError:
                pass
        return
    persisted = _persisted_running()
    if persisted is None or persisted.get("kind") != "deploy" or persisted.get("template_id") != template_id:
        return
    pid = int(persisted["pid"])
    try:
        process_group = int(persisted.get("process_group") or pid)
        if hasattr(os, "killpg"):
            os.killpg(process_group, 15)
        else:
            os.kill(pid, 15)
    except OSError:
        pass
    try:
        with connect() as conn:
            conn.execute(
                "UPDATE jobs SET state='stopped', ended_at=datetime('now'), heartbeat_at=datetime('now'), error=? WHERE id=?",
                ("deploy interrotto prima dello stop dell’app", persisted["id"]),
            )
    except sqlite3.OperationalError:
        pass


def extract_endpoint() -> str | None:
    """L'URL dell'endpoint nel log del deploy, se presente."""
    if _task is None:
        return None
    for line in reversed(_task.log):
        if URL_PREFIX in line and URL_SUFFIX in line:
            token = line[line.index(URL_PREFIX) :].split()[0].rstrip(",)")
            return token
    return None


# --- stato REALE via CLI (fonte di verità per la UI) ---------------------------
# Il pannello non deve fidarsi della memoria interna (chi ha fatto il deploy?
# la UI o un terminale?): interroga il CLI e deriva l'endpoint dall'account.
# `--json` invece di scrapare la tabella Rich: col testo, due template il cui
# nome inizia entrambe con "tabularium-" (es. "tabularium-vllm" e
# "tabularium-paddleocr-vl") diventano lo stesso "tabularium-…" troncato a
# terminali stretti e sono indistinguibili — riprodotto. `--json` restituisce
# "description" per intero, corrispondenza esatta.
_app_cache: dict[str, tuple[dict, float]] = {}
_APP_TTL = 60.0


def _invalidate_cache() -> None:
    global _app_cache
    _app_cache = {}


def _query_modal_cli(template_id: str | None = None) -> dict:
    """Workspace, stato dell'app serverless ed endpoint per una template, con cache breve."""
    import json as jsonlib  # noqa: PLC0415
    import re  # noqa: PLC0415
    import time  # noqa: PLC0415

    template = _template(template_id)
    cached = _app_cache.get(template.id)
    if cached is not None and time.time() - cached[1] < _APP_TTL:
        return cached[0]

    out: dict = {"workspace": None, "app_state": None, "endpoint": None}
    exe = _find_modal()
    if exe is not None:
        try:
            res = subprocess.run(  # noqa: S603
                [exe, "token", "info"], capture_output=True, text=True, timeout=30
            )
            if res.returncode == 0:
                m = re.search(r"Workspace:\s*([^\s(]+)", res.stdout)
                if m:
                    out["workspace"] = m.group(1)
                    # L'URL delle web function è deterministico dall'account.
                    out["endpoint"] = (
                        f"https://{m.group(1)}--{template.app_name}-serve.modal.run"
                    )
        except Exception:  # noqa: BLE001
            pass
        try:
            res = subprocess.run(  # noqa: S603
                [exe, "app", "list", "--json"], capture_output=True, text=True, timeout=60
            )
            if res.returncode == 0:
                apps = jsonlib.loads(res.stdout)
                # Più tentativi restano in lista come "stopped": si preferisce
                # "deployed" appena presente per quella app esatta.
                states = [
                    str(a.get("state", "")).lower()
                    for a in apps
                    if a.get("description") == template.app_name
                ]
                out["app_state"] = (
                    "deployed" if "deployed" in states else ("stopped" if "stopped" in states else None)
                )
        except Exception:  # noqa: BLE001
            pass
    # L'endpoint è servito solo se l'app è davvero deployata.
    if out["app_state"] != "deployed":
        out["endpoint"] = None
    _app_cache[template.id] = (out, time.time())
    return out


def status(template_id: str | None = None) -> dict:
    template = _template(template_id)
    task = _task
    persisted = None if task is not None and not task.done else _persisted_running()
    cli_state = _query_modal_cli(template.id)
    task_matches = (
        task is not None and (task.kind == "setup" or task.template_id == template.id)
    ) or (
        persisted is not None
        and (persisted["kind"] == "setup" or persisted.get("template_id") == template.id)
    )
    if task_matches and task is not None:
        task_payload = {
            "kind": task.kind,
            "done": task.done,
            "ok": task.ok,
            "log": task.log[-80:],
        }
    elif task_matches and persisted is not None:
        log: list[str] = []
        if persisted.get("log_path"):
            try:
                log = Path(persisted["log_path"]).read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            except OSError:
                pass
        task_payload = {
            "kind": persisted["kind"],
            "done": False,
            "ok": None,
            "log": log,
        }
    else:
        task_payload = None
    return {
        "cli": _find_modal() is not None,
        "token": token_configured(),
        "templates": list_templates(),
        "template": template.id,
        "workspace": cli_state["workspace"],
        "app_state": cli_state["app_state"],
        "endpoint": cli_state["endpoint"],
        "task": task_payload,
    }
