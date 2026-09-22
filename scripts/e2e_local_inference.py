"""E2E dell'inferenza locale: dal verdetto hardware all'export.

Il percorso locale dipende dalla macchina, quindi il test non presuppone un
runtime: legge il verdetto da `/api/system/info`, sceglie un modello che
*questa* macchina sa servire e, se non ce n'è nessuno, esce dicendolo — su una
macchina senza GPU e senza Apple Silicon non c'è niente da verificare, e non è
un fallimento.

Richiede che il backend sia in esecuzione. La prima volta può scaricare i pesi
del runtime locale (vLLM o MLX): minuti, non secondi.

Uso: TABULARIUM_E2E_URL=http://127.0.0.1:8787 python3 scripts/e2e_local_inference.py
"""
from __future__ import annotations

import os
import sys
import time

import requests

BASE = os.environ.get("TABULARIUM_E2E_URL", "http://127.0.0.1:8787").rstrip("/")
PORT = int(os.environ.get("TABULARIUM_E2E_PORT", "8897"))


def _fail(message: str) -> int:
    print(f"e2e local inference FAILED: {message}", file=sys.stderr)
    return 1


def main() -> int:
    health = requests.get(f"{BASE}/api/health", timeout=10)
    health.raise_for_status()

    info = requests.get(f"{BASE}/api/system/info", timeout=10).json()
    compute = info["capabilities"]["local_compute"]
    runtimes = compute["usable_runtimes"]
    print(
        f"macchina: {compute['platform']}/{compute['arch']} · "
        f"{compute['memory_gb']} GB · runtime locali: {runtimes or 'nessuno'}"
    )
    if not runtimes:
        print("nessun runtime locale su questa macchina: niente da verificare")
        return 0

    models = requests.get(f"{BASE}/api/models", timeout=30).json()["items"]
    local = [m for m in models if m.get("local", {}).get("runnable")]
    if not local:
        print("nessun modello eseguibile in locale qui: niente da verificare")
        return 0
    # Il primo che non richiede un percorso ufficiale a parte: il flusso è lo
    # stesso, ma il test resta veloce.
    model = next((m for m in local if m["adapter_id"] != "paddleocr-vl"), local[0])
    print(f"modello locale: {model['adapter_id']} via {model['local']['runtime']}")

    projects = requests.get(f"{BASE}/api/projects", timeout=10).json()["items"]
    if not projects:
        return _fail("nessun progetto: serve almeno una pagina registrata")
    project_id = projects[0]["id"]
    pages = requests.get(f"{BASE}/api/projects/{project_id}/pages", timeout=10).json()["items"]
    if not pages:
        return _fail(f"il progetto {project_id} non ha pagine")

    # --- servire il modello ---------------------------------------------------
    started = requests.post(
        f"{BASE}/api/models/{model['adapter_id']}/serve/start",
        json={"port": PORT},
        timeout=60,
    )
    if started.status_code not in (200, 202):
        return _fail(f"avvio del server locale: {started.status_code} {started.text[:300]}")

    deadline = time.time() + float(os.environ.get("TABULARIUM_E2E_SERVE_TIMEOUT", "1800"))
    status: dict = {}
    while time.time() < deadline:
        status = requests.get(f"{BASE}/api/models/serve/status", timeout=10).json()
        if status.get("ready"):
            break
        if status.get("phase") == "failed":
            return _fail(f"server locale in errore: {status.get('error')}")
        time.sleep(5)
    if not status.get("ready"):
        return _fail(f"server locale non pronto entro il tempo: {status.get('phase')}")
    print(f"server locale pronto su :{PORT}")

    # --- riconoscere una pagina ----------------------------------------------
    run = requests.post(
        f"{BASE}/api/projects/{project_id}/recognition-runs",
        json={
            "page_ids": [pages[0]["id"]],
            "engine": "model",
            "mode": "merge",
            "model_mode": "native",
            "stop_policy": "none",
        },
        timeout=60,
    )
    if run.status_code not in (200, 202):
        return _fail(f"avvio della run: {run.status_code} {run.text[:300]}")
    run_id = run.json()["id"]

    deadline = time.time() + float(os.environ.get("TABULARIUM_E2E_RUN_TIMEOUT", "1800"))
    detail: dict = {}
    while time.time() < deadline:
        detail = requests.get(f"{BASE}/api/projects/{project_id}/recognition-runs/{run_id}", timeout=10).json()
        if detail["state"] != "running":
            break
        time.sleep(5)
    if detail.get("state") == "running":
        return _fail("la run non è terminata entro il tempo")

    items = detail.get("items", [])
    errors = [f"pagina {i['page_id']}: {(i['error'] or '')[:200]}" for i in items if i["state"] == "failed"]
    print(f"run #{run_id}: {detail['state']} · {detail['succeeded_pages']}/{detail['total_pages']} pagine")
    if not detail["succeeded_pages"]:
        return _fail(f"nessuna pagina riconosciuta · {errors}")

    # --- esportare ------------------------------------------------------------
    exported = requests.get(
        f"{BASE}/api/projects/{project_id}/recognition-runs/{run_id}/export",
        params={"scope": "reviewed", "format": "text"},
        timeout=120,
    )
    if exported.status_code != 200 or not exported.content.strip():
        return _fail(f"export vuoto o fallito: {exported.status_code}, {len(exported.content)} byte")
    print(f"export: {len(exported.content)} byte")

    requests.post(f"{BASE}/api/models/serve/stop", timeout=60)
    print("e2e local inference OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
