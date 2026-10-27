"""Modelli aggiunti liberamente dall'utente: un repo Hugging Face qualsiasi,
non uno degli adapter con protocollo OCR verificato in `model_adapters.py`.

Stesso principio di LM Studio: chiunque può digitare un repo e provarlo.
Nessun blocco per dimensione — solo un avviso (v. `model_registry.vram_warning`)
quando il checkpoint rischia di non entrare nella GPU rilevata. Il download e
il serve locale riusano `model_registry`/`serve_manager` esistenti: l'unica
differenza è che l'adapter non è uno dei singleton fissi in `_ADAPTERS`, ma
costruito al volo da questa tabella (v. `model_adapters.get_adapter`).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import uuid
import json
from pathlib import Path

from .. import config
from ..adapters import custom_models as modelstore
from ..db import connect

_ID_PREFIX = "custom-"


def _is_python_console_script(path: str | Path) -> bool:
    """Un comando pip è uno script con shebang; `swift` su macOS è un binario.

    Senza questo controllo `shutil.which("swift")` trova il compilatore Apple
    e il merge LoRA prova a eseguire `swift export`, fallendo con un errore
    che non ha niente a che vedere con ms-swift.
    """
    try:
        with Path(path).open("rb") as stream:
            return stream.read(2) == b"#!"
    except OSError:
        return False


def _toolchain_cli(directory: str, command: str) -> str | None:
    """Il comando del toolchain: prima il `.venv` del checkout, poi il PATH."""
    if directory:
        candidate = Path(directory) / '.venv' / 'bin' / command
        if candidate.exists():
            return str(candidate)
    found = shutil.which(command)
    if found and command == "swift" and not _is_python_console_script(found):
        return None
    return found


def toolchain_status(recipe) -> dict:
    """Dove trovare il comando di merge *su questa macchina*.

    Il merge LoRA non è un'operazione del backend: è il comando ufficiale del
    toolchain e deve esistere dove gira il dashboard. Questa sonda serve alla
    UI per dirlo prima di far cliccare un pulsante che fallirebbe.
    """
    toolchain = str(getattr(recipe, "toolchain", "ms-swift") or "ms-swift")
    if toolchain == "llama-factory":
        cli = _toolchain_cli(config.LLAMAFACTORY_DIR, "llamafactory-cli")
        return {
            "toolchain": toolchain,
            "available": bool(cli),
            "command": "llamafactory-cli",
            "hint": "" if cli else (
                "LLaMA-Factory non è installato qui: imposta TABULARIUM_LLAMAFACTORY_DIR "
                "sul checkout usato per il training."
            ),
        }
    if toolchain == "paddleformers":
        cli = _toolchain_cli(config.PADDLEFORMERS_DIR, "paddleformers-cli")
        return {
            "toolchain": toolchain,
            "available": bool(cli),
            "command": "paddleformers-cli",
            "hint": "" if cli else (
                "PaddleFormers non è installato qui: imposta TABULARIUM_PADDLEFORMERS_DIR "
                "sul checkout usato per il training."
            ),
        }
    python = str(config.TRAIN_PYTHON or "").strip()
    candidate = Path(python).with_name("swift") if python else None
    cli = (
        str(candidate)
        if candidate and candidate.exists() and _is_python_console_script(candidate)
        else _toolchain_cli("", "swift")
    )
    return {
        "toolchain": toolchain,
        "available": bool(cli),
        "command": "swift",
        "hint": "" if cli else (
            "ms-swift non è installato qui: installa l'ambiente del modello "
            "oppure esegui il merge sulla GPU remota."
        ),
    }


def _has_weights(directory: Path) -> bool:
    return (directory / 'config.json').exists() and any(
        path.is_file()
        for pattern in ('*.safetensors', '*.bin')
        for path in directory.glob(pattern)
    )


def _merged_checkpoint(adapter_path: Path, recipe, cfg: dict, *, log_file: Path | None = None) -> Path:
    """Unisce un adapter LoRA in un checkpoint HF servibile da vLLM.

    Ogni toolchain ha il *proprio* comando di merge: `swift export` non sa
    leggere un adapter LLaMA-Factory e `llamafactory-cli export` non sa leggere
    uno ms-swift. Usare il comando sbagliato produce un errore oscuro al
    momento di servire il modello, quindi il merge è per ricetta.
    """
    output = adapter_path.parent.parent / f"{adapter_path.name}-merged"
    if _has_weights(output):
        return output
    toolchain = getattr(recipe, 'toolchain', 'ms-swift')
    base_model = str(cfg.get('model_path') or cfg.get('model') or getattr(recipe, 'hf_repo', '') or '')

    if toolchain == 'llama-factory':
        cli = _toolchain_cli(config.LLAMAFACTORY_DIR, 'llamafactory-cli')
        if not cli:
            raise ValueError(
                'llamafactory-cli non disponibile: imposta TABULARIUM_LLAMAFACTORY_DIR '
                'sul checkout usato per il training, per unire l’adapter'
            )
        output.mkdir(parents=True, exist_ok=True)
        command = [
            cli, 'export',
            '--model_name_or_path', base_model,
            '--adapter_name_or_path', str(adapter_path),
            '--template', getattr(recipe, 'llamafactory_template', '') or 'glm_ocr',
            '--export_dir', str(output),
            '--trust_remote_code', 'true',
        ]
        cwd = config.LLAMAFACTORY_DIR or None
    elif toolchain == 'paddleformers':
        # La config di export è un percorso relativo al checkout: senza la
        # cartella il comando non può funzionare.
        if not config.PADDLEFORMERS_DIR:
            raise ValueError(
                'TABULARIUM_PADDLEFORMERS_DIR non impostato: serve il checkout '
                'PaddleFormers usato per il training, per unire l’adapter'
            )
        cli = _toolchain_cli(config.PADDLEFORMERS_DIR, 'paddleformers-cli')
        if not cli:
            raise ValueError(
                'paddleformers-cli non disponibile: installa PaddleFormers nel checkout '
                'indicato da TABULARIUM_PADDLEFORMERS_DIR'
            )
        export_config = getattr(recipe, 'paddleformers_export', '') or ''
        if not export_config:
            raise ValueError('ricetta PaddleFormers senza config ufficiale di export')
        # L'export ufficiale legge il checkpoint dalla `output_dir`: passiamo la
        # cartella che contiene `checkpoint-N`, e il risultato unito finisce in
        # `<output_dir>/export`.
        merged_root = adapter_path.parent
        command = [
            cli, 'export', export_config,
            f'model_name_or_path={base_model}',
            f'output_dir={merged_root}',
        ]
        output = merged_root / 'export'
        cwd = config.PADDLEFORMERS_DIR or None
    else:
        swift = _toolchain_cli('', 'swift')
        if not swift:
            python = str(config.TRAIN_PYTHON or '').strip()
            candidate = Path(python).with_name('swift') if python else None
            swift = (
                str(candidate)
                if candidate and candidate.exists() and _is_python_console_script(candidate)
                else None
            )
        if not swift:
            raise ValueError('swift non disponibile: impossibile unire il checkpoint LoRA')
        output.mkdir(parents=True, exist_ok=True)
        command = [
            swift, 'export', '--adapters', str(adapter_path),
            '--merge_lora', 'true', '--output_dir', str(output),
        ]
        cwd = None

    detail = ""
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("ab") as stream:
            stream.write(f">> merge locale ({toolchain}) con il comando ufficiale\n".encode())
            stream.flush()
            result = subprocess.run(
                command, stdout=stream, stderr=subprocess.STDOUT, text=True,
                timeout=3600, check=False, cwd=cwd,
            )
        try:
            detail = log_file.read_text(encoding="utf-8", errors="replace").strip()[-600:]
        except OSError:
            detail = ""
    else:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=3600, check=False, cwd=cwd
        )
        detail = (result.stderr or result.stdout).strip()[-600:]
    if result.returncode != 0 or not _has_weights(output):
        raise ValueError(
            f'unione LoRA fallita ({toolchain}){": " + detail if detail else ""}'
        )
    return output


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "model"


def _unique_id(conn, base_slug: str) -> str:
    candidate = f"{_ID_PREFIX}{base_slug}"
    if not modelstore.exists(candidate, conn):
        return candidate
    # Collisione (stesso repo/nome aggiunto due volte): un suffisso corto
    # basta, non serve un contatore leggibile.
    return f"{candidate}-{uuid.uuid4().hex[:6]}"


def _out(row) -> dict:
    d = dict(row)
    d["trust_remote_code"] = bool(d["trust_remote_code"])
    return d


def register_training_checkpoint(
    project_id: int,
    run_id: str,
    *,
    merged_path: Path | str | None = None,
    registered_type: str | None = None,
    log_file: Path | None = None,
) -> dict:
    """Registra l'ultimo checkpoint di una run terminata come modello locale.

    Il checkpoint resta nella cartella della run: non viene duplicato e la
    provenienza rimane tracciabile. L'adapter base conserva prompt e formato
    di export; cambia soltanto il percorso dei pesi serviti.
    """
    run_id = str(run_id or '').strip()
    if not run_id or Path(run_id).name != run_id or run_id in {'.', '..'}:
        raise ValueError('run di training non valida')
    run_dir = (config.DATA_DIR / str(int(project_id)) / 'runs' / run_id).resolve()
    runs_root = (config.DATA_DIR / str(int(project_id)) / 'runs').resolve()
    if run_dir.parent != runs_root or not run_dir.is_dir():
        raise ValueError('run di training non trovata')
    try:
        meta = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('metadati della run non leggibili') from exc
    if meta.get('state') != 'finished':
        raise ValueError('il checkpoint è disponibile solo per una run terminata con successo')
    cfg = meta.get('config') or {}
    base_id = str(cfg.get('adapter_id') or '').strip()
    if not base_id:
        raise ValueError('la run non dichiara l’adapter del modello base')
    from .model_adapters import get_adapter
    try:
        base = get_adapter(base_id)
    except ValueError as exc:
        raise ValueError('adapter base della run non riconosciuto') from exc
    checkpoint_root = (run_dir / 'checkpoints').resolve()
    # LLaMA-Factory e PaddleFormers scrivono i checkpoint in una sottocartella
    # (`<output_dir>/<run>`), ms-swift con `--no_add_version` direttamente:
    # la ricerca è ricorsiva perché la struttura la decide il toolchain.
    candidates = [
        path for path in checkpoint_root.rglob('checkpoint-*')
        if path.is_dir()
        and (
            (path / 'adapter_config.json').exists()
            and any(path.glob(pattern) for pattern in ('adapter_model*.safetensors', 'adapter_model*.bin'))
            or (path / 'config.json').exists()
            and any(path.glob(pattern) for pattern in ('*.safetensors', '*.bin'))
        )
    ] if checkpoint_root.is_dir() else []
    if not candidates:
        raise ValueError('la run non contiene un checkpoint completo')
    checkpoint = max(candidates, key=lambda path: int(path.name.split('-', 1)[1]) if path.name.split('-', 1)[1].isdigit() else -1)
    train_type = str(cfg.get('train_type') or 'lora').strip().lower()
    source_path = checkpoint
    effective_type = train_type
    if train_type == 'lora':
        from . import train_recipes

        recipe = train_recipes.get_recipe(base_id)
        if recipe is not None and not recipe.merge_supported:
            raise ValueError(
                f"la ricetta {recipe.toolchain} non documenta un merge LoRA: "
                "serve un checkpoint full-SFT"
            )
        if merged_path is not None:
            # Il merge è già stato fatto altrove (tipicamente sulla GPU remota,
            # dove vivono toolchain e pesi base) e scaricato: qui si registra
            # soltanto, senza rieseguire il comando di export.
            candidate = Path(merged_path).expanduser().resolve()
            if not _has_weights(candidate):
                raise ValueError('checkpoint unito non valido: mancano i pesi o config.json')
            source_path = candidate
        else:
            source_path = _merged_checkpoint(checkpoint, recipe, cfg, log_file=log_file)
        # Dopo il merge il file registrato è un modello completo: il serving
        # non deve tentare di applicare di nuovo l'adapter.
        effective_type = 'full'
    if registered_type:
        effective_type = registered_type
    with connect() as conn:
        existing = modelstore.by_source_run(run_id, conn)
        if existing:
            return _out(existing)
        adapter_id = _unique_id(conn, _slug(f'{base.capabilities.display_name}-{run_id}'))
        display_name = f'{base.capabilities.display_name} · fine-tuned {run_id}'
        served_name = f'tabularium-{_slug(base_id)}-{_slug(run_id)}'
        modelstore.insert(
            {
                "id": adapter_id,
                "display_name": display_name,
                "hf_repo": base.capabilities.hf_repo or base_id,
                "hf_revision": base.capabilities.hf_revision,
                "served_model_name": served_name,
                "trust_remote_code": 0,
                "max_model_len": base.capabilities.max_model_len,
                "gpu_memory_utilization": None,
                "extra_args": "",
                "source_path": str(source_path),
                "base_adapter_id": base_id,
                "source_run_id": run_id,
                "source_train_type": effective_type,
            },
            merged=True,
            conn=conn,
        )
        row = modelstore.get(adapter_id, conn)
    return _out(row)


def list_all() -> list[dict]:
    # `model_adapters.list_adapters()` chiama questa funzione anche da
    # contesti che non hanno mai inizializzato il DB (es. test puri sugli
    # adapter): un DB non ancora migrato equivale a "nessun modello custom",
    # non a un errore (stesso principio di `model_registry.install_state`).
    try:
        return [_out(row) for row in modelstore.list_all()]
    except Exception:  # noqa: BLE001
        return []


def get(adapter_id: str) -> dict | None:
    if not adapter_id.startswith(_ID_PREFIX):
        return None
    try:
        row = modelstore.get(adapter_id)
    except Exception:  # noqa: BLE001
        return None
    return _out(row) if row else None


def create(payload: dict) -> dict:
    hf_repo = str(payload.get("hf_repo") or "").strip()
    if not hf_repo or "/" not in hf_repo:
        raise ValueError("hf_repo deve essere un repo Hugging Face nel formato 'org/nome'")
    display_name = str(payload.get("display_name") or hf_repo).strip()
    served_model_name = str(payload.get("served_model_name") or hf_repo.split("/")[-1]).strip()
    max_model_len = payload.get("max_model_len")
    gpu_memory_utilization = payload.get("gpu_memory_utilization")
    with connect() as conn:
        adapter_id = _unique_id(conn, _slug(display_name or hf_repo))
        modelstore.insert(
            {
                "id": adapter_id,
                "display_name": display_name,
                "hf_repo": hf_repo,
                "hf_revision": str(payload.get("hf_revision") or "").strip() or None,
                "served_model_name": served_model_name,
                "trust_remote_code": 1 if payload.get("trust_remote_code") else 0,
                "max_model_len": int(max_model_len) if max_model_len else None,
                "gpu_memory_utilization": float(gpu_memory_utilization) if gpu_memory_utilization else None,
                "extra_args": str(payload.get("extra_args") or "").strip(),
            },
            conn=conn,
        )
        row = modelstore.get(adapter_id, conn)
    return _out(row)


def delete(adapter_id: str) -> None:
    row = get(adapter_id)
    if row is None:
        raise ValueError(f"modello custom sconosciuto: {adapter_id}")
    weights_dir = config.MODELS_DIR / adapter_id
    if weights_dir.exists():
        shutil.rmtree(weights_dir)
    modelstore.delete(adapter_id)
