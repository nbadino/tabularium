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
from ..db import connect

_ID_PREFIX = "custom-"


def _toolchain_cli(directory: str, command: str) -> str | None:
    """Il comando del toolchain: prima il `.venv` del checkout, poi il PATH."""
    if directory:
        candidate = Path(directory) / '.venv' / 'bin' / command
        if candidate.exists():
            return str(candidate)
    return shutil.which(command)


def _has_weights(directory: Path) -> bool:
    return (directory / 'config.json').exists() and any(
        path.is_file()
        for pattern in ('*.safetensors', '*.bin')
        for path in directory.glob(pattern)
    )


def _merged_checkpoint(adapter_path: Path, recipe, cfg: dict) -> Path:
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
            swift = str(candidate) if candidate and candidate.exists() else None
        if not swift:
            raise ValueError('swift non disponibile: impossibile unire il checkpoint LoRA')
        output.mkdir(parents=True, exist_ok=True)
        command = [
            swift, 'export', '--adapters', str(adapter_path),
            '--merge_lora', 'true', '--output_dir', str(output),
        ]
        cwd = None

    result = subprocess.run(
        command, capture_output=True, text=True, timeout=3600, check=False, cwd=cwd
    )
    if result.returncode != 0 or not _has_weights(output):
        detail = (result.stderr or result.stdout).strip()[-600:]
        raise ValueError(
            f'unione LoRA fallita ({toolchain}){": " + detail if detail else ""}'
        )
    return output


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "model"


def _unique_id(conn, base_slug: str) -> str:
    candidate = f"{_ID_PREFIX}{base_slug}"
    if not conn.execute("SELECT 1 FROM custom_models WHERE id=?", (candidate,)).fetchone():
        return candidate
    # Collisione (stesso repo/nome aggiunto due volte): un suffisso corto
    # basta, non serve un contatore leggibile.
    return f"{candidate}-{uuid.uuid4().hex[:6]}"


def _out(row) -> dict:
    d = dict(row)
    d["trust_remote_code"] = bool(d["trust_remote_code"])
    return d


def register_training_checkpoint(project_id: int, run_id: str) -> dict:
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
    registered_type = train_type
    if train_type == 'lora':
        from . import train_recipes

        recipe = train_recipes.get_recipe(base_id)
        if recipe is not None and not recipe.merge_supported:
            raise ValueError(
                f"la ricetta {recipe.toolchain} non documenta un merge LoRA: "
                "serve un checkpoint full-SFT"
            )
        source_path = _merged_checkpoint(checkpoint, recipe, cfg)
        # Dopo il merge il file registrato è un modello completo: il serving
        # non deve tentare di applicare di nuovo l'adapter.
        registered_type = 'full'
    with connect() as conn:
        existing = conn.execute('SELECT * FROM custom_models WHERE source_run_id=?', (run_id,)).fetchone()
        if existing:
            return _out(existing)
        adapter_id = _unique_id(conn, _slug(f'{base.capabilities.display_name}-{run_id}'))
        display_name = f'{base.capabilities.display_name} · fine-tuned {run_id}'
        served_name = f'tabularium-{_slug(base_id)}-{_slug(run_id)}'
        conn.execute(
            'INSERT INTO custom_models '
            '(id, display_name, hf_repo, hf_revision, served_model_name, trust_remote_code, '
            'max_model_len, gpu_memory_utilization, extra_args, source_path, base_adapter_id, source_run_id, source_train_type) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (adapter_id, display_name, base.capabilities.hf_repo or base_id, base.capabilities.hf_revision,
             served_name, 0, base.capabilities.max_model_len, None, '', str(source_path), base_id, run_id,
             registered_type),
        )
        row = conn.execute('SELECT * FROM custom_models WHERE id=?', (adapter_id,)).fetchone()
    return _out(row)


def list_all() -> list[dict]:
    # `model_adapters.list_adapters()` chiama questa funzione anche da
    # contesti che non hanno mai inizializzato il DB (es. test puri sugli
    # adapter): un DB non ancora migrato equivale a "nessun modello custom",
    # non a un errore (stesso principio di `model_registry.install_state`).
    try:
        with connect() as conn:
            return [
                _out(row)
                for row in conn.execute("SELECT * FROM custom_models ORDER BY created_at DESC").fetchall()
            ]
    except Exception:  # noqa: BLE001
        return []


def get(adapter_id: str) -> dict | None:
    if not adapter_id.startswith(_ID_PREFIX):
        return None
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM custom_models WHERE id=?", (adapter_id,)).fetchone()
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
        conn.execute(
            "INSERT INTO custom_models "
            "(id, display_name, hf_repo, hf_revision, served_model_name, trust_remote_code, "
            " max_model_len, gpu_memory_utilization, extra_args) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                adapter_id,
                display_name,
                hf_repo,
                str(payload.get("hf_revision") or "").strip() or None,
                served_model_name,
                1 if payload.get("trust_remote_code") else 0,
                int(max_model_len) if max_model_len else None,
                float(gpu_memory_utilization) if gpu_memory_utilization else None,
                str(payload.get("extra_args") or "").strip(),
            ),
        )
        row = conn.execute("SELECT * FROM custom_models WHERE id=?", (adapter_id,)).fetchone()
    return _out(row)


def delete(adapter_id: str) -> None:
    row = get(adapter_id)
    if row is None:
        raise ValueError(f"modello custom sconosciuto: {adapter_id}")
    weights_dir = config.MODELS_DIR / adapter_id
    if weights_dir.exists():
        shutil.rmtree(weights_dir)
    with connect() as conn:
        conn.execute("DELETE FROM custom_models WHERE id=?", (adapter_id,))
