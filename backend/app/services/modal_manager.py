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

import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

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

    def drain(self) -> None:
        """Consuma l'output disponibile senza bloccare (log live in UI)."""
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            self.log.append(line.rstrip())
            if len(self.log) > 500:
                del self.log[: len(self.log) - 500]


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
    _token_cache = None  # il setup/deploy può aver cambiato lo stato del token
    _invalidate_cache()  # idem per lo stato dell'app


def _start(
    kind: str,
    args: list[str],
    env: dict[str, str] | None = None,
    template_id: str | None = None,
) -> None:
    global _task
    with _task_lock:
        if _task is not None and not _task.done:
            raise RuntimeError("un task Modal è già in corso")
        exe = _find_modal()
        if exe is None:
            raise RuntimeError(
                "CLI modal non trovata: installarla con "
                "`backend/.venv/bin/pip install modal` e riavviare Tabularium"
            )
        proc = subprocess.Popen(  # noqa: S603
            [exe, *args],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        _task = ModalTask(kind=kind, proc=proc, template_id=template_id)
        threading.Thread(target=_watch, args=(_task,), daemon=True).start()


def is_deploying() -> bool:
    task = _current()
    return task is not None and task.kind == "deploy" and not task.done


def current_kind() -> str | None:
    task = _current()
    return task.kind if task is not None and not task.done else None


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


def start_setup() -> None:
    """Autenticazione: apre il browser per approvare il token."""
    _start("setup", ["token", "new"])


def start_deploy(
    template_id: str | None = None,
    api_key: str | None = None,
    keep_warm: bool = False,
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
    _start("deploy", ["deploy", str(template.script)], env=env, template_id=template.id)


def stop_app(template_id: str | None = None) -> None:
    """Ferma l'app Modal e termina i container attivi."""
    template = _template(template_id)
    exe = _find_modal()
    if exe is None:
        raise RuntimeError("CLI Modal non trovata")
    _start(
        "stop",
        ["app", "stop", "-y", template.app_name],
        env=dict(os.environ),
        template_id=template.id,
    )


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
    cli_state = _query_modal_cli(template.id)
    task_matches = task is not None and (task.kind == "setup" or task.template_id == template.id)
    return {
        "cli": _find_modal() is not None,
        "token": token_configured(),
        "templates": list_templates(),
        "template": template.id,
        "workspace": cli_state["workspace"],
        "app_state": cli_state["app_state"],
        "endpoint": cli_state["endpoint"],
        "task": None
        if not task_matches
        else {
            "kind": task.kind,
            "done": task.done,
            "ok": task.ok,
            "log": task.log[-80:],
        },
    }
