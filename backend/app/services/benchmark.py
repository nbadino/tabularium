"""Benchmark multi-modello sulle pagine gold (M17).

Misura **più modelli sulle stesse pagine** riusando le metriche di
`services/evaluate.py` (IoU per label a 0.5 e 0.75, Levenshtein dell'ordine di
lettura, struttura+CER tabelle, CER/WER testo). La verità resta l'annotazione
umana: il riferimento sono le pagine approvate/esportate, mai l'output di un
altro modello.

Proprietà di contratto:
  - **nessuna scrittura nel DB**: il benchmark non crea blocchi, griglie o
    annotazioni. Scrive solo un report su disco (`eval/bench_<ts>/`), così non
    può sporcare il corpus né diventare dato di training per sbaglio;
  - **cache per (adapter, model, page, annotation_revision)**: un refresh non
    ripaga chiamate già fatte; cambiare l'annotazione di una pagina la invalida;
  - **misura il percorso in esercizio** (prompt/parser dell'adapter), non la
    pipeline nativa del produttore: è l'output che l'utente otterrebbe davvero.
"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import dataset_builder as builder
from . import evaluate as evmod
from . import inference as infmod
from . import model_adapters
from .. import config
from .i18n import msg
from . import table_hint as table_hint_svc

# Quanto costa un modello remoto, per 1M token (input, output). Serve solo a
# rendere leggibile la spesa prima di rilanciare: i locali non hanno voce.
_PRICES: dict[str, tuple[float, float]] = {
    "gemini-argon": (4.0, 20.0),
}


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value or "")[:80] or "model"


# Versione della logica di misura: bump quando cambia il modo in cui un output
# viene interpretato (es. bridge delle label), per invalidare la cache.
_CACHE_VERSION = 3


def _cache_dir(project_id: int) -> Path:
    d = builder._project_dir(project_id) / "eval" / "bench_cache"  # noqa: SLF001
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(project_id: int, target: dict, page_id: int, revision: int) -> Path:
    # La versione invalida la cache quando cambia la logica di misura (es. il
    # bridge delle label): senza, un risultato vecchio resterebbe per sempre.
    key = f"v{_CACHE_VERSION}__{_slug(target['adapter_id'])}__{_slug(target['model'])}__{page_id}__{revision}.json"
    return _cache_dir(project_id) / key


def _target_client(target: dict):
    """Client per un candidato, indipendente dal profilo attivo."""
    try:
        adapter = model_adapters.get_adapter(target["adapter_id"])
    except ValueError as exc:
        raise ValueError(f"adapter sconosciuto: {target['adapter_id']}") from exc
    api_key = target.get("api_key")
    # Un modello chiuso via gateway OpenCode non ha una chiave nel vault: la
    # sottoscrizione la conserva sulla macchina, come per il profilo attivo.
    if not api_key and (
        target.get("provider") == "opencode"
        or getattr(adapter.capabilities, "serve_backend", "") == "opencode-gateway"
    ):
        from . import opencode_gateway

        api_key = opencode_gateway.read_api_key() or None
    return infmod.VllmClient(
        url=target.get("url") or None,
        model=target.get("model") or None,
        api_key=api_key,
        adapter=adapter,
        provider=target.get("provider"),
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _local_runtime(adapter) -> str | None:
    from . import hardware

    plan = hardware.plan_local(
        getattr(adapter.capabilities, "local_runtimes", ()) or (),
        approx_size_gb=getattr(adapter.capabilities, "approx_size_gb", None),
        mlx_weight_factor=getattr(adapter.capabilities, "local_mlx_weight_factor", None),
    )
    return plan["runtime"] if plan["runnable"] else None


def _start_local(adapter_id: str, log_dir: Path) -> dict | None:
    """Avvia il server locale di un adapter su una porta dedicata, o `None`.

    Non passa da `serve_manager`: quello cambia il profilo di inferenza attivo
    (e romperebbe una run di riconoscimento in corso, es. Argon). Qui il server
    è isolato — porta libera, nessuna scrittura di config — e viene spento dal
    chiamante appena finita la misura. Un modello alla volta.
    """
    from . import mlx_runtime, mineru_mlx_runtime, hardware

    try:
        adapter = model_adapters.get_adapter(adapter_id)
    except ValueError:
        return None
    if _local_runtime(adapter) != hardware.RUNTIME_MLX:
        return None
    repo = getattr(adapter.capabilities, "local_mlx_repo", "") or ""
    if not repo:
        return None
    port = _free_port()
    if adapter_id == "unlimited-ocr":
        argv = mlx_runtime.unlimited_ocr_serve_argv(repo, port=port)
    elif adapter_id == "mineru2.5":
        argv = mineru_mlx_runtime.serve_argv(repo, port=port)
    else:
        argv = mlx_runtime.serve_argv(repo, port=port)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = open(log_dir / f"serve_{adapter_id}.log", "wb")
    except OSError:
        return None
    proc = subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT)
    return {
        "proc": proc,
        "port": port,
        "repo": repo,
        "url": f"http://127.0.0.1:{port}/v1",
        # MinerU parla col gateway nativo su `/parse`, non solo OpenAI.
        "native_url": f"http://127.0.0.1:{port}" if adapter_id == "mineru2.5" else None,
    }


def _wait_ready(port: int, timeout: float = 600.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - server ancora in avvio
            pass
        time.sleep(2)
    return False


def _endpoint_alive(url: str, timeout: float = 2.0) -> bool:
    """Vero se un server OpenAI-compatibile risponde già su `url`."""
    import urllib.request

    try:
        with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _stop_local(local: dict) -> None:
    """Spegne il server locale del benchmark.

    `mlx_vlm.server` non sempre esce su SIGTERM: si aspetta poco e poi si passa
    a SIGKILL, così nessun modello resta in memoria a fine misura.
    """
    proc = local.get("proc")
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        pass


def selectable_pages(project_id: int) -> list[dict]:
    """Pagine utilizzabili come riferimento nel benchmark.

    Una pagina è bench-ready quando i suoi blocchi sono **tutti confermati**
    (nessuna bozza non verificata): è la stessa condizione dell'approvazione, e
    impedisce che un output del modello conti come verità. Le pagine approvate o
    esportate lo sono per costruzione.
    """
    page_data = builder.collect_pages_with_blocks(project_id)
    items: list[dict] = []
    for pid, data in sorted(page_data.items()):
        blocks = data["blocks"]
        if not blocks:
            continue
        unconfirmed = sum(1 for b in blocks if not b["confirmed"])
        status = data["page"]["status"]
        ready = unconfirmed == 0 or status in {"approved", "exported"}
        items.append({
            "page_id": pid,
            "rel_path": data["page"]["rel_path"],
            "status": status,
            "blocks": len(blocks),
            "unconfirmed": unconfirmed,
            "ready": ready,
        })
    return items


def _selected_pages(project_id: int, page_ids: list[int] | None, limit: int, lang: str) -> tuple[dict, dict[str, int]]:
    """Pagine scelte per il benchmark e revisione corrente per la cache."""
    page_data = builder.collect_pages_with_blocks(project_id)
    if not page_data:
        raise ValueError(msg("no_pages_annotated", lang))
    selectable = {p["page_id"]: p for p in selectable_pages(project_id) if p["ready"]}
    if not selectable:
        raise ValueError(msg("no_gold_pages", lang))
    if page_ids:
        wanted = [pid for pid in page_ids if pid in selectable]
        missing = [pid for pid in page_ids if pid not in selectable]
        if missing:
            raise ValueError(f"pagine non pronte per il benchmark: {', '.join(map(str, missing))}")
        chosen = wanted
    else:
        chosen = sorted(selectable)[:limit]
    page_data = {pid: page_data[pid] for pid in chosen}
    revisions = {pid: int(page_data[pid]["page"]["annotation_revision"] or 0) for pid in chosen}
    return page_data, revisions


def _evaluate_target(
    project_id: int,
    target: dict,
    page_data: dict,
    revisions: dict[str, int],
    *,
    with_text: bool,
    use_cache: bool,
    lang: str,
    table_hint: str,
    schema_labels: list | None = None,
    on_page=None,
    table_only: bool = False,
) -> dict:
    warnings: list[str] = []
    target = dict(target)
    local: dict | None = None
    # Endpoint locale (o assente) di un adapter eseguibile qui: il benchmark
    # avvia il server da solo, uno alla volta, e lo spegne a fine misura. Il
    # profilo di inferenza attivo (es. Argon) non viene mai toccato.
    url = str(target.get("url") or "")
    endpoint_is_local = (not url) or ("127.0.0.1" in url) or ("localhost" in url)
    # Se l'endpoint locale indicato risponde già, si usa quello: non si carica
    # un secondo modello in memoria.
    if endpoint_is_local and url and _endpoint_alive(url):
        endpoint_is_local = False
    if endpoint_is_local:
        local = _start_local(target["adapter_id"], _cache_dir(project_id))
        if local is not None:
            # Il pid finisce nello stato: se il backend riparte a metà run, il
            # server locale resta orfano e `benchmark_status` lo spegne.
            _write_state(project_id, serve_pid=local["proc"].pid)
            if not _wait_ready(local["port"]):
                warnings.append(f"server locale '{target['adapter_id']}' non pronto")
            target["url"] = local["url"]
            target["model"] = local["repo"]
            target["provider"] = "local"

    try:
        try:
            client = _target_client(target)
        except ValueError as exc:
            return {"target": target, "error": str(exc), "pages": []}
        if local and local.get("native_url"):
            client.native_url = local["native_url"]

        reachable = False
        try:
            reachable = bool(client.ping())
        except Exception:  # noqa: BLE001 - un ping fallito non deve fermare il benchmark
            reachable = False
        if not reachable:
            warnings.append(msg("vllm_ping", lang, url=client.url))

        pages_result: list[dict] = []
        seconds = 0.0
        total_pages = len(page_data)
        for page_index, (pid, data) in enumerate(page_data.items(), start=1):
            if on_page is not None:
                on_page(page_index, total_pages)
            revision = revisions[pid]
            cache_path = _cache_path(project_id, target, pid, revision)
            cached = None
            if use_cache and cache_path.exists():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    cached = None
            if cached is not None:
                pages_result.append(cached)
                seconds += float(cached.get("_seconds") or 0.0)
                continue
            started = time.time()
            res = evmod._evaluate_page(  # noqa: SLF001 - metriche riusate, non reimplementate
                client, data, with_text, warnings, lang=lang, table_hint=table_hint,
                schema_labels=schema_labels, table_only=table_only,
            )
            res["_seconds"] = round(time.time() - started, 2)
            seconds += res["_seconds"]
            pages_result.append(res)
            if use_cache and "error" not in res:
                try:
                    cache_path.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass

        ok = [p for p in pages_result if "error" not in p]
        texts = [t for p in ok for t in p["text"]]
        tables = [t for p in ok for t in p["tables"]]
        layout = evmod._agg_layout(ok)  # noqa: SLF001
        layout_strict = evmod._agg_layout(ok, key="layout_strict")  # noqa: SLF001
        order = evmod._agg_order(ok)  # noqa: SLF001
        return {
            "target": target,
            "adapter_id": target["adapter_id"],
            "model": client.model,
            "url": client.url,
            "reachable": reachable,
            "pages_evaluated": len(ok),
            "seconds": round(seconds, 1),
            "aggregates": {
                "layout": layout,
                "layout_strict": layout_strict,
                "order": order,
                "text": {
                    "n": len(texts),
                    "mean_cer": evmod._mean([t["cer"] for t in texts]),  # noqa: SLF001
                    "mean_wer": evmod._mean([t["wer"] for t in texts]),  # noqa: SLF001
                },
                "tables": {
                    "n": len(tables),
                    "structure_ok_pct": (
                        evmod._mean([1.0 if t["structure_ok"] else 0.0 for t in tables]) * 100  # noqa: SLF001
                        if tables else None
                    ),
                    "mean_cell_cer": evmod._mean([t["cell_cer"] for t in tables]),  # noqa: SLF001
                    "mean_teds": evmod._mean([t["teds"] for t in tables if t.get("teds") is not None]),  # noqa: SLF001
                },
            },
            "pages": pages_result,
            "warnings": warnings,
        }
    finally:
        if local is not None:
            _stop_local(local)
            _write_state(project_id, serve_pid=None)


def _score(entry: dict) -> tuple:
    """Chiave di classifica: layout F1 a 0.75, poi ordine, poi tabelle, testo."""
    agg = entry.get("aggregates") or {}
    layout_strict = (agg.get("layout_strict") or {}).get("f1") or 0.0
    order = (agg.get("order") or {}).get("mean_levenshtein_norm")
    order = 1.0 - order if order is not None else 0.0
    structure = (agg.get("tables") or {}).get("mean_teds") or 0.0
    cer = (agg.get("text") or {}).get("mean_cer")
    text = 1.0 - cer if cer is not None else 0.0
    return (layout_strict, order, structure, text)


def _fmt(value, digits: int = 3, suffix: str = "") -> str:
    """Numero per l'HTML, o «—» quando l'aggregato è assente (nessuna pagina)."""
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


def _render_html(report: dict) -> str:
    rows = []
    for rank, entry in enumerate(report["leaderboard"], start=1):
        agg = entry["aggregates"]
        rows.append(
            "<tr>"
            f"<td>{rank}</td><td>{entry['adapter_id']}</td><td>{entry['model']}</td>"
            f"<td>{_fmt((agg.get('layout_strict') or {}).get('f1'))}</td>"
            f"<td>{_fmt((agg.get('layout') or {}).get('f1'))}</td>"
            f"<td>{_fmt((agg.get('order') or {}).get('mean_levenshtein_norm'))}</td>"
            f"<td>{_fmt((agg.get('tables') or {}).get('structure_ok_pct'), 1, '%')}</td>"
            f"<td>{_fmt((agg.get('tables') or {}).get('mean_teds'))}</td>"
            f"<td>{_fmt((agg.get('text') or {}).get('mean_cer'))}</td>"
            f"<td>{entry['seconds']}</td>"
            "</tr>"
        )
    return (
        "<!doctype html><meta charset='utf-8'><title>Benchmark</title>"
        "<style>body{font:13px system-ui;margin:24px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}</style>"
        f"<h1>Benchmark modelli — progetto {report['project_id']}</h1>"
        f"<p>Gold: {report['gold_pages']} pagine approvate · scope {report['scope']} · "
        f"testo {'incluso' if report['config']['with_text'] else 'escluso'}</p>"
        "<table><tr><th>#</th><th>Adapter</th><th>Modello</th><th>F1@0.75</th>"
        "<th>F1@0.5</th><th>Ordine (↓)</th><th>Tabelle ok</th><th>TEDS</th><th>CER testo</th>"
        "<th>Secondi</th></tr>" + "".join(rows) + "</table>"
    )


def run_benchmark(
    project_id: int,
    targets: list[dict],
    *,
    page_ids: list[int] | None = None,
    limit: int = 10,
    with_text: bool = True,
    use_cache: bool = True,
    lang: str = "it",
    on_progress=None,
    on_page=None,
    on_target=None,
    table_only: bool = False,
) -> dict:
    """Esegue i candidati sulle stesse pagine gold e restituisce il report.

    Non scrive nel DB: solo `eval/bench_<ts>/{report.json,report.html}`.
    `on_progress(index, total, adapter_id)` viene chiamato prima di ogni modello.
    """
    if not targets:
        raise ValueError("serve almeno un modello da confrontare")
    page_data, revisions = _selected_pages(project_id, page_ids, limit, lang)

    table_hint = evmod._project_table_hint(project_id)  # noqa: SLF001
    schema_labels = evmod._schema_labels(project_id)  # noqa: SLF001
    entries = []
    for index, target in enumerate(targets):
        if on_progress is not None:
            on_progress(index, len(targets), target.get("adapter_id", ""))
        entry = _evaluate_target(
            project_id, target, page_data, revisions,
            with_text=with_text, use_cache=use_cache, lang=lang, table_hint=table_hint,
            schema_labels=schema_labels, on_page=on_page, table_only=table_only,
        )
        entries.append(entry)
        # Un modello finito è un risultato: lo si dichiara subito (la tabella si
        # popola man mano, senza aspettare la fine di tutti i candidati).
        if on_target is not None:
            on_target(entry)
    leaderboard = sorted(
        [e for e in entries if "error" not in e], key=_score, reverse=True
    )
    for entry in entries:
        if entry.get("adapter_id") in _PRICES:
            entry["cost_note"] = "prezzo per 1M token: input {:.2f} / output {:.2f}".format(*_PRICES[entry["adapter_id"]])

    report = {
        "project_id": project_id,
        "kind": "benchmark",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "gold_pages": len(page_data),
        "scope": "gold",
        "config": {"with_text": with_text, "limit": limit, "use_cache": use_cache},
        "leaderboard": leaderboard,
        "targets": entries,
    }
    out_dir = builder._project_dir(project_id) / "eval" / f"bench_{int(time.time())}"  # noqa: SLF001
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.html").write_text(_render_html(report), encoding="utf-8")
    report["bench_dir"] = str(out_dir)
    return report


# --- esecuzione in background -----------------------------------------------
# Il benchmark dura minuti: farlo dentro la richiesta HTTP significava perdere lo
# stato a ogni refresh della pagina. Gira quindi in un thread, con lo stato su
# disco (`eval/bench_state.json`): un refresh lo ritrova, e il report resta.

_ACTIVE: dict[int, dict] = {}
_LOCK = threading.Lock()


def _state_path(project_id: int) -> Path:
    d = builder._project_dir(project_id) / "eval"  # noqa: SLF001
    d.mkdir(parents=True, exist_ok=True)
    return d / "bench_state.json"


def _write_state(project_id: int, **fields) -> None:
    path = _state_path(project_id)
    state: dict = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
    state.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
    try:
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def benchmark_status(project_id: int) -> dict:
    """Stato del benchmark del progetto, persistito su disco."""
    path = _state_path(project_id)
    if not path.exists():
        return {"state": "idle"}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "idle"}
    # Un riavvio del backend uccide il thread: uno stato «running» senza thread
    # vivo è un'interruzione, non un lavoro in corso. Il server locale avviato
    # dal benchmark resta orfano: lo si spegne qui (pid registrato nello stato).
    if state.get("state") == "running" and project_id not in _ACTIVE:
        pid = state.get("serve_pid")
        if isinstance(pid, int):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        state["state"] = "error"
        state["error"] = "benchmark interrotto (backend riavviato)"
        state["serve_pid"] = None
    return state


def reconcile_benchmarks() -> None:
    """All'avvio: un benchmark `running` non ha thread vivo (processo nuovo) e
    il server locale che aveva avviato è orfano. Lo spegne e marca interrotto."""
    root = config.ROOT_DIR / "projects"
    if not root.is_dir():
        return
    for state_path in root.glob("*/eval/bench_state.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if state.get("state") != "running":
            continue
        pid = state.get("serve_pid")
        if isinstance(pid, int):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        state.update(state="error", error="benchmark interrotto (backend riavviato)", serve_pid=None)
        try:
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def start_benchmark(
    project_id: int,
    targets: list[dict],
    *,
    page_ids: list[int] | None = None,
    limit: int = 10,
    with_text: bool = True,
    use_cache: bool = True,
    lang: str = "it",
    table_only: bool = False,
) -> dict:
    """Avvia il benchmark in background. Idempotente: se gira già, lo dichiara."""
    if not targets:
        raise ValueError("serve almeno un modello da confrontare")
    with _LOCK:
        if project_id in _ACTIVE:
            return {"started": False, "already_running": True, **benchmark_status(project_id)}
        _write_state(
            project_id, state="running", started_at=datetime.now(timezone.utc).isoformat(),
            total=len(targets), index=0, current_adapter=targets[0].get("adapter_id", ""),
            error=None, report_dir=None, gold_pages=None, results=[], page_total=None, page_index=0,
        )

        def worker() -> None:
            results: list[dict] = []

            def record(entry: dict) -> None:
                if "error" in entry:
                    return
                results.append({
                    key: entry.get(key)
                    for key in (
                        "adapter_id", "model", "url", "reachable",
                        "pages_evaluated", "seconds", "aggregates",
                    )
                })
                _write_state(project_id, results=results)

            try:
                report = run_benchmark(
                    project_id, targets,
                    page_ids=page_ids, limit=limit, with_text=with_text,
                    use_cache=use_cache, lang=lang,
                    on_progress=lambda i, _t, adapter: _write_state(
                        project_id, index=i, current_adapter=adapter, page_index=0, page_total=None
                    ),
                    on_page=lambda i, n: _write_state(project_id, page_index=i, page_total=n),
                    on_target=record,
                    table_only=table_only,
                )
                _write_state(
                    project_id, state="done", index=len(targets),
                    report_dir=report.get("bench_dir"), gold_pages=report.get("gold_pages"),
                    error=None,
                )
            except Exception as exc:  # noqa: BLE001 - l'errore va nello stato, non perso
                _write_state(project_id, state="error", error=str(exc))
            finally:
                _ACTIVE.pop(project_id, None)

        thread = threading.Thread(target=worker, name=f"benchmark-{project_id}", daemon=True)
        _ACTIVE[project_id] = {"thread": thread}
        thread.start()
    return {"started": True, **benchmark_status(project_id)}
