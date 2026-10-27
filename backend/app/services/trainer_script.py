"""Generazione degli script di training, uno per toolchain.

Il generatore non conosce i modelli per nome: legge la ricetta dell'adapter
(`train_recipes.py`) e produce il comando che la **documentazione ufficiale**
di quel modello prescrive. Oggi i toolchain sono tre:

- ``ms-swift`` (MonkeyOCRv2, Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2,
  dots.ocr, MinerU2.5). MonkeyOCRv2 usa il fork ufficiale in
  ``parsing/train/ms-swift``, dove il flag è ``--train_type``; l'ms-swift
  upstream usa ``--tuner_type``. Il flag sta nella ricetta.
- ``llama-factory`` (GLM-OCR): ``llamafactory-cli train <yaml>``.
- ``paddleformers`` (PaddleOCR-VL): ``paddleformers-cli train <config>
  key=value``.

Se una ricetta non è eseguibile il generatore **fallisce**, indicando la doc
ufficiale: non produce mai uno script che sembra giusto e non lo è.
"""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .dataset_builder import _project_dir
from . import train_recipes as recipes
from .training_views import build_view

# Default storici del riferimento MonkeyOCRv2: restano il fallback quando una
# chiamata non specifica nulla, ma ogni modello prende i propri dalla ricetta.
DEFAULTS: dict = {
    "model": "zenosai/MonkeyOCRv2-B-Parsing",
    "model_path": "",
    "train_type": "lora",
    "lora_rank": 8,
    "lora_alpha": 32,
    "epochs": 1.0,
    "learning_rate": 1e-5,
    "batch_size": 4,
    "grad_accum": 1,
    "max_length": 16384,
    "max_pixels": 1003520,
    "gpus": "0",
    "nproc": 1,
    "eval_steps": 200,
    "save_steps": 500,
    "ssh_train_repo": "",
    "ssh_python": "",
}

FAMILIES = ("layout", "text_rec", "table", "formula")

# Famiglia canonica → chiave dei prompt della ricetta.
_FAMILY_PROMPT_KEY = {
    "layout": "layout",
    "text_rec": "text",
    "table": "table",
    "formula": "formula",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def remote_shell_path(value: str) -> str:
    """Quota un percorso remoto lasciando espandere `$HOME`.

    Il noleggio Vast restituisce `$HOME/tabularium-train/...`: quotarlo con
    `shlex.quote` lo renderebbe letterale e `cd`/`dirname` cercherebbero una
    cartella chiamata `$HOME`. Le virgolette doppie espandono la variabile e
    proteggono comunque gli spazi.
    """
    text = str(value or "")
    if text.startswith("$HOME/") or text.startswith("${HOME}/") or text.startswith("~/"):
        if text.startswith("~/"):
            text = "$HOME/" + text[2:]
        return f'"{text}"'
    return shlex.quote(text)


def _dataset_source(project_id: int) -> tuple[Path, str | None]:
    """Risolvi l'ultimo snapshot immutabile, con fallback al dataset pubblico."""
    base = _project_dir(project_id) / "dataset"
    if not base.exists():
        raise FileNotFoundError("dataset non presente: eseguire prima la build (M4)")
    report_path = base / "report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            snapshot_id = report.get("snapshot_id")
            snapshot_dir = Path(
                report.get("snapshot_dir") or base / "snapshots" / str(snapshot_id)
            )
            if snapshot_id and snapshot_dir.is_dir():
                return snapshot_dir, str(snapshot_id)
        except (OSError, TypeError, ValueError):
            pass
    return base, None


def resolve_config(cfg: dict) -> tuple[recipes.TrainRecipe, dict, str]:
    """Ricetta + configurazione effettiva + tipo di training validato.

    I valori passati esplicitamente vincono; quelli assenti li detta la
    ricetta, con i suoi default ufficiali per il tipo di training. Un tipo non
    ammesso dal modello (es. LoRA dove il vendor documenta solo full-SFT)
    fallisce qui, non a metà del run.
    """
    adapter_id = str(cfg.get("adapter_id") or "monkeyocrv2-parsing")
    recipe = recipes.get_recipe(adapter_id)
    if recipe is None:
        raise ValueError(
            f"nessuna ricetta di training per '{adapter_id}': "
            "il modello non è addestrabile o non è a pesi aperti"
        )
    if not recipe.trainable:
        reason = recipe.note or "il vendor non distribuisce codice di training"
        raise ValueError(f"'{adapter_id}' non è addestrabile: {reason}")
    if not recipe.script_ready:
        raise NotImplementedError(
            f"ricetta '{recipe.toolchain}' di '{adapter_id}' non ancora eseguibile "
            f"da Tabularium; doc ufficiale: {recipe.official_doc}"
        )
    train_type = str(cfg.get("train_type") or (recipe.train_types[0] if recipe.train_types else "lora"))
    if train_type not in recipe.train_types:
        raise ValueError(
            f"'{adapter_id}' non supporta il training '{train_type}' "
            f"(ammessi: {', '.join(recipe.train_types)}); doc ufficiale: {recipe.official_doc}"
        )
    effective = dict(DEFAULTS)
    effective.update(recipe.defaults_for(train_type))
    effective.update({key: value for key, value in cfg.items() if value is not None})
    # Il checkpoint di default è quello della ricetta, non il MonkeyOCRv2
    # storico: senza questa riga un modello scelto per adapter ma senza
    # `model` esplicito scaricherebbe il checkpoint sbagliato.
    if not str(cfg.get("model_path") or "").strip() and not str(cfg.get("model") or "").strip():
        effective["model"] = recipe.hf_repo
    return recipe, effective, train_type


def prepare_training_files(
    project_id: int,
    destination_dir: Path | None = None,
    adapter_id: str = "monkeyocrv2-parsing",
    train_type: str = "lora",
    *,
    images_prefix: str | None = None,
) -> tuple[Path, Path]:
    """Materializza la vista dati della ricetta e ne restituisce train/val.

    La vista vive nella cartella del run: due run non si sovrascrivono, e lo
    script generato legge esattamente i file che il manifest dichiara.
    """
    recipe = recipes.get_recipe(adapter_id)
    if recipe is None or not recipe.trainable:
        raise ValueError(f"nessuna vista dati per '{adapter_id}'")
    source, _snapshot_id = _dataset_source(project_id)
    target = destination_dir or (_project_dir(project_id) / "dataset")
    view = build_view(recipe, source, target, images_prefix=images_prefix)
    # Manifest della vista: dice quali famiglie sono entrate, con quali
    # prompt e cosa era vuoto. Il run lo allega, così un checkpoint ha la sua
    # provenienza dati senza doverla ricostruire.
    (target / "view.json").write_text(
        json.dumps(view.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return view.train, view.val


def _python_bootstrap(cfg: dict, *, remote: bool, toolchain: str) -> str:
    """Interprete/ambiente che rende visibile il comando del toolchain.

    `TRAIN_PYTHON` (o `ssh_python`) è autorevole: se è impostato, la sua
    cartella entra nel PATH, così `swift`, `llamafactory-cli` e
    `paddleformers-cli` vengono dallo stesso ambiente. Senza, si prova il
    `.venv` del checkout del toolchain e solo per MonkeyOCRv2 si ricade
    sull'ambiente conda ufficiale.
    """
    python_path = str(cfg.get("ssh_python") or "") if remote else config.TRAIN_PYTHON
    if python_path:
        return f'export PATH="$(dirname {remote_shell_path(python_path)}):$PATH"\n'
    if toolchain == "ms-swift" and str(cfg.get("adapter_id") or "") == "monkeyocrv2-parsing":
        return (
            'if command -v conda >/dev/null 2>&1; then\n'
            '  source "$(conda info --base)/etc/profile.d/conda.sh"\n'
            f'  conda activate {config.TRAIN_ENV}\n'
            'fi\n'
        )
    return ""


def _workdir(cfg: dict, *, remote: bool, toolchain: str, adapter_id: str) -> str:
    """Directory da cui eseguire il comando (config del toolchain relativa)."""
    if remote:
        return str(cfg.get("ssh_train_repo") or "")
    if toolchain == "llama-factory":
        return config.LLAMAFACTORY_DIR
    if toolchain == "paddleformers":
        return config.PADDLEFORMERS_DIR
    if adapter_id == "monkeyocrv2-parsing":
        repo = Path(config.TRAIN_REPO) / "parsing" / "train" if config.TRAIN_REPO else None
        return str(repo) if repo and repo.is_dir() else ""
    return ""


def _preamble(
    cfg: dict, *, remote: bool, toolchain: str, workdir: str, nproc_env: bool = True
) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"',
        f'export CUDA_VISIBLE_DEVICES="{cfg.get("gpus", "0")}"',
    ]
    if nproc_env:
        lines.append(f'export NPROC_PER_NODE={int(cfg.get("nproc") or 1)}')
    lines += [
        "",
        _python_bootstrap(cfg, remote=remote, toolchain=toolchain).rstrip(),
        "",
        'RUN_DIR="$(cd "$(dirname "$0")" && pwd)"',
    ]
    if workdir:
        lines.append(f"cd {remote_shell_path(workdir)}")
    lines.append("")
    return "\n".join(lines)


def _shell_path(value: str) -> str:
    """Percorso per lo shell: `$RUN_DIR` resta espandibile, il resto è quotato."""
    if value.startswith("$RUN_DIR"):
        return f'"{value}"'
    return shlex.quote(value)


def _resume_block(output_dir: str) -> str:
    """Resume ufficiale: il `checkpoint-N` più alto nella output_dir."""
    return f'''output_dir={_shell_path(output_dir)}
resume_options=""
if [ -d "$output_dir" ]; then
  latest=$(find "$output_dir" -maxdepth 1 -type d -name 'checkpoint-[0-9]*' -print 2>/dev/null \\
    | sed -E 's/.*checkpoint-([0-9]+)$/\\1 &/' | sort -n | tail -1 | cut -d' ' -f2-)
  if [ -n "$latest" ]; then
    resume_options="--resume_from_checkpoint $latest"
    echo ">> resume da $latest"
  fi
fi
'''


def _attention(flag: str, *, packing: bool | None, padding_free: bool | None) -> str:
    """`attn_impl` + fallback dichiarato quando flash-attn non è installato.

    La doc ufficiale presume flash-attn. Se manca, `swift` con
    `flash_attention_2` fallisce subito: meglio un fallback esplicito nel log.
    `packing` e `padding_free` richiedono flash-attn per essere corretti: con
    il fallback si spengono, invece di lasciare due flag che non possono
    funzionare (`padding_free` senza flash-attn non isola le sequenze).
    """
    packing_value = "" if packing is None else ("True" if packing else "False")
    padding_value = "" if padding_free is None else ("True" if padding_free else "False")
    lines = [f'PACKING="{packing_value}"', f'PADDING_FREE="{padding_value}"']
    if flag == "flash_attention_2":
        lines.append('ATTN="flash_attention_2"')
        lines.append('if ! python -c "import flash_attn" >/dev/null 2>&1; then')
        lines.append('  echo ">> flash-attn non disponibile: uso attn_impl=eager, packing/padding_free off"')
        lines.append('  ATTN="eager"; PACKING="False"; PADDING_FREE="False"')
        lines.append("fi")
    elif flag == "flash_attn":
        lines.append('ATTN="flash_attn"')
        lines.append('if ! python -c "import flash_attn" >/dev/null 2>&1; then')
        lines.append('  echo ">> flash-attn non disponibile: uso attn_impl=sdpa, packing/padding_free off"')
        lines.append('  ATTN="sdpa"; PACKING="False"; PADDING_FREE="False"')
        lines.append("fi")
    elif flag:
        lines.append(f'ATTN="{flag}"')
    else:
        lines.append('ATTN=""')
    return "\n".join(lines) + "\n"


def _dataset_args(cfg: dict, *, remote: bool, train_file: Path, val_file: Path) -> tuple[str, str]:
    if remote:
        return "dataset/train.jsonl", "dataset/val.jsonl"
    return str(train_file), str(val_file)


def _output_dir(cfg: dict, recipe: recipes.TrainRecipe, run_dir: Path, train_type: str, *, remote: bool) -> str:
    explicit = str(cfg.get("output_dir") or "").strip()
    if explicit:
        return explicit
    name = f"{recipe.adapter_id}_{train_type}"
    if remote:
        return f"checkpoints/{name}"
    return str(run_dir / "checkpoints" / name)


# ---------------------------------------------------------------------------
# ms-swift
# ---------------------------------------------------------------------------
def _ms_swift_script(cfg: dict, recipe: recipes.TrainRecipe, run_dir: Path, train_file: Path, val_file: Path) -> str:
    remote = str(cfg.get("executor", "local")).strip().lower() in {"ssh", "vast", "runpod"}
    train_type = str(cfg.get("train_type") or "lora")
    flags = recipe.flags
    model_ref = str(cfg.get("model_path") or "").strip() or str(cfg.get("model") or recipe.hf_repo)
    train_arg, val_arg = _dataset_args(cfg, remote=remote, train_file=train_file, val_file=val_file)
    out_dir = _output_dir(cfg, recipe, run_dir, train_type, remote=remote)
    if remote:
        # Lo script fa `cd` nel repo del toolchain: i percorsi relativi alla
        # directory sincronizzata (`dataset/`, `checkpoints/`) diventerebbero
        # percorsi del repo. `$RUN_DIR` è calcolato prima del `cd`.
        train_arg = "$RUN_DIR/dataset/train.jsonl"
        val_arg = "$RUN_DIR/dataset/val.jsonl"
        out_dir = f"$RUN_DIR/checkpoints/{recipe.adapter_id}_{train_type}"
    workdir = _workdir(cfg, remote=remote, toolchain="ms-swift", adapter_id=recipe.adapter_id)

    env_lines = []
    if flags.get("image_max_token_num"):
        env_lines.append(f'export IMAGE_MAX_TOKEN_NUM={int(flags["image_max_token_num"])}')
    if cfg.get("max_pixels") and "max_pixels" in recipe.defaults:
        env_lines.append(f'export MAX_PIXELS={int(cfg["max_pixels"])}')
    # La cache dei pesi è condivisa e persistente, non una cartella della run:
    # un checkpoint è immutabile e riscaricarlo a ogni run costa decine di GB
    # (e su un host remoto la run vive in /tmp, quindi si perde al riavvio).
    # `model_cache` permette di puntarla esplicitamente (disco dati, NFS…).
    explicit_cache = str(cfg.get("model_cache") or "").strip()
    if remote:
        env_lines.append(
            f"export MODELSCOPE_CACHE={shlex.quote(explicit_cache)}"
            if explicit_cache
            else 'export MODELSCOPE_CACHE="${MODELSCOPE_CACHE:-$HOME/.cache/modelscope}"'
        )
    else:
        env_lines.append(
            f"export MODELSCOPE_CACHE={shlex.quote(explicit_cache or str(config.ROOT_DIR / 'cache' / 'modelscope'))}"
        )

    args: list[str] = [
        f"  --model {shlex.quote(model_ref)} \\",
        f"  --model_type {recipe.swift_model_type} \\",
        f"  --template {recipe.swift_template} \\",
    ]
    if cfg.get("use_hf"):
        # I repo delle ricette sono HuggingFace: su un host fuori dalla Cina
        # ModelScope è solo un mirror più lento e può non avere il checkpoint.
        args.append("  --use_hf true \\")
    if flags.get("attn_impl"):
        args.append('  --attn_impl "$ATTN" \\')
    args += [
        f'  --dataset "{train_arg}" \\',
        f'  --val_dataset "{val_arg}" \\',
        # Lo split è per pagina e lo fa Tabularium: ms-swift non deve
        # ritagliarsi un 1% dal training (default 0.01) e cambiare le pagine.
        "  --split_dataset_ratio 0.0 \\",
        "  --load_from_cache_file True \\",
        # Con DeepSpeed i worker del DataLoader non riescono a serializzare
        # l'hook del motore (`Can't pickle ... _module_forward_post_hook`):
        # il run muore al primo batch. 0 worker è più lento ma esegue; il
        # valore ufficiale 4 resta quando DeepSpeed non è in uso.
        f"  --dataloader_num_workers {0 if (flags.get('deepspeed') and not cfg.get('disable_deepspeed')) else 4} \\",
        "  --dataset_num_proc 4 \\",
        "  --dataset_shuffle True \\",
        "  --streaming False \\",
        f"  --max_length {int(cfg['max_length'])} \\",
        "  --truncation_strategy right \\",
    ]
    if cfg.get("max_pixels") and "max_pixels" in recipe.defaults:
        args.append(f"  --max_pixels {int(cfg['max_pixels'])} \\")
    if "packing" in flags:
        args.append('  --packing "$PACKING" \\')
    if "padding_free" in flags:
        args.append('  --padding_free "$PADDING_FREE" \\')
    args.append(f"  {recipe.tuner_flag} {train_type} \\")
    if train_type == "lora":
        args += [
            f"  --lora_rank {int(cfg['lora_rank'])} \\",
            f"  --lora_alpha {int(cfg['lora_alpha'])} \\",
            "  --target_modules all-linear \\",
        ]
    for key, cli in (
        ("freeze_vit", "freeze_vit"),
        ("freeze_aligner", "freeze_aligner"),
        ("freeze_llm", "freeze_llm"),
    ):
        if key in flags:
            value = cfg.get(key, flags[key]) if key == "freeze_vit" else flags[key]
            args.append(f"  --{cli} {'True' if value else 'False'} \\")
    if "gradient_checkpointing" in flags:
        args.append(f"  --gradient_checkpointing {'True' if flags['gradient_checkpointing'] else 'False'} \\")
    if "vit_gradient_checkpointing" in flags:
        args.append(
            f"  --vit_gradient_checkpointing {'True' if flags['vit_gradient_checkpointing'] else 'False'} \\"
        )
    args += [
        f"  --torch_dtype {flags.get('torch_dtype', 'bfloat16')} \\",
    ]
    if flags.get("deepspeed") and not cfg.get("disable_deepspeed"):
        args.append(f"  --deepspeed {flags['deepspeed']} \\")
    args += [
        f"  --per_device_train_batch_size {int(cfg['batch_size'])} \\",
        f"  --per_device_eval_batch_size {int(flags.get('eval_batch_size', 2))} \\",
        f"  --gradient_accumulation_steps {int(cfg['grad_accum'])} \\",
        f"  --num_train_epochs {float(cfg['epochs']):g} \\",
        f"  --learning_rate {float(cfg['learning_rate']):g} \\",
        "  --warmup_ratio 0.05 \\",
        "  --lr_scheduler_type cosine \\",
        f"  --eval_steps {int(cfg['eval_steps'])} \\",
        f"  --save_steps {int(cfg['save_steps'])} \\",
        "  --save_total_limit 2 \\",
        "  --logging_steps 5 \\",
        "  --no_add_version \\",
        '  --output_dir "$output_dir" \\',
        "  $resume_options",
    ]
    command = "swift sft \\\n" + "\n".join(args)
    return (
        _preamble(cfg, remote=remote, toolchain="ms-swift", workdir=workdir)
        + "\n".join(env_lines)
        + "\n\n"
        + _attention(
            str(flags.get("attn_impl") or ""),
            packing=flags.get("packing"),
            padding_free=flags.get("padding_free"),
        )
        + "\n"
        + _resume_block(out_dir)
        + "\n"
        + command
        + "\n"
    )


# ---------------------------------------------------------------------------
# LLaMA-Factory (GLM-OCR)
# ---------------------------------------------------------------------------
def _llamafactory_yaml(cfg: dict, recipe: recipes.TrainRecipe, train_type: str) -> str:
    flags = recipe.flags
    lines = [
        f"model_name_or_path: {cfg.get('model_path') or cfg.get('model') or recipe.hf_repo}",
        f"template: {recipe.llamafactory_template}",
        f"trust_remote_code: {'true' if flags.get('trust_remote_code', True) else 'false'}",
        "",
        "stage: sft",
        "do_train: true",
        "do_eval: true",
        f"finetuning_type: {train_type}",
    ]
    if train_type == "lora":
        lines += [
            f"lora_rank: {int(cfg['lora_rank'])}",
            f"lora_target: {flags.get('lora_target', 'all')}",
        ]
    else:
        lines += [
            f"freeze_vision_tower: {'true' if flags.get('freeze_vision_tower', True) else 'false'}",
            "freeze_multi_modal_projector: "
            f"{'true' if flags.get('freeze_multi_modal_projector', True) else 'false'}",
            f"freeze_language_model: {'true' if flags.get('freeze_language_model', False) else 'false'}",
        ]
    lines += [
        "",
        f"dataset: tabularium_{recipe.adapter_id}_train",
        f"eval_dataset: tabularium_{recipe.adapter_id}_val",
        'dataset_dir: "$RUN_DIR/dataset"',
        f"cutoff_len: {int(cfg['max_length'])}",
        "preprocessing_num_workers: 8",
        "",
        'output_dir: "$RUN_DIR/checkpoints/' + f"{recipe.adapter_id}_{train_type}" + '"',
        "logging_steps: 5",
        f"save_steps: {int(cfg['save_steps'])}",
        "save_total_limit: 2",
        "plot_loss: true",
        "overwrite_output_dir: true",
        "report_to: none",
        "",
        f"per_device_train_batch_size: {int(cfg['batch_size'])}",
        "per_device_eval_batch_size: 2",
        f"gradient_accumulation_steps: {int(cfg['grad_accum'])}",
        f"learning_rate: {float(cfg['learning_rate']):g}",
        f"num_train_epochs: {float(cfg['epochs']):g}",
        f"lr_scheduler_type: {flags.get('lr_scheduler_type', 'cosine')}",
        f"warmup_ratio: {float(flags.get('warmup_ratio', 0.1)):g}",
        "bf16: true",
        f"eval_steps: {int(cfg['eval_steps'])}",
        "eval_strategy: steps",
    ]
    return "\n".join(lines) + "\n"


def _llama_factory_script(cfg: dict, recipe: recipes.TrainRecipe, run_dir: Path, train_file: Path, val_file: Path) -> str:
    remote = str(cfg.get("executor", "local")).strip().lower() in {"ssh", "vast", "runpod"}
    train_type = str(cfg.get("train_type") or "lora")
    workdir = _workdir(cfg, remote=remote, toolchain="llama-factory", adapter_id=recipe.adapter_id)
    nproc = int(cfg.get("nproc") or 1)
    yaml_text = _llamafactory_yaml(cfg, recipe, train_type)
    launch = 'DISABLE_VERSION_CHECK=1 llamafactory-cli train "$RUN_DIR/train_llamafactory.yaml"'
    if nproc > 1:
        launch = (
            f"DISABLE_VERSION_CHECK=1 FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE={nproc} \\\n"
            '  llamafactory-cli train "$RUN_DIR/train_llamafactory.yaml" \\\n'
            "  --deepspeed examples/deepspeed/ds_z2_config.json"
        )
    return (
        _preamble(cfg, remote=remote, toolchain="llama-factory", workdir=workdir)
        + "\n"
        # Heredoc senza quoting: `$RUN_DIR` è l'unica espansione presente e
        # deve diventare un percorso assoluto prima che LLaMA-Factory legga lo
        # YAML (che non espande variabili).
        + 'cat > "$RUN_DIR/train_llamafactory.yaml" <<YAML\n'
        + yaml_text
        + "YAML\n\n"
        + launch
        + "\n"
    )


# ---------------------------------------------------------------------------
# PaddleFormers (PaddleOCR-VL)
# ---------------------------------------------------------------------------
def _paddleformers_script(cfg: dict, recipe: recipes.TrainRecipe, run_dir: Path, train_file: Path, val_file: Path) -> str:
    remote = str(cfg.get("executor", "local")).strip().lower() in {"ssh", "vast", "runpod"}
    train_type = str(cfg.get("train_type") or "full")
    config_file = recipe.paddleformers_config.get(train_type)
    if not config_file:
        raise NotImplementedError(
            f"PaddleFormers non documenta il training '{train_type}' per '{recipe.adapter_id}'; "
            f"doc ufficiale: {recipe.official_doc}"
        )
    workdir = _workdir(cfg, remote=remote, toolchain="paddleformers", adapter_id=recipe.adapter_id)
    train_arg, val_arg = _dataset_args(cfg, remote=remote, train_file=train_file, val_file=val_file)
    out_dir = _output_dir(cfg, recipe, run_dir, train_type, remote=remote)
    flags = recipe.flags
    pre_alloc = flags.get("pre_alloc_memory") or {}
    pre_alloc_value = pre_alloc.get(train_type) if isinstance(pre_alloc, dict) else pre_alloc

    # Su host remoto i percorsi locali non esistono: lo script gira dalla
    # cartella sincronizzata, quindi `$RUN_DIR` va lasciato espandere dalla
    # shell. Quotarlo con shlex lo renderebbe letterale.
    def run_path(value: str) -> str:
        return f'"$RUN_DIR/{value}"' if remote else shlex.quote(value)

    overrides = [
        f"model_name_or_path={shlex.quote(str(cfg.get('model_path') or cfg.get('model') or recipe.hf_repo))}",
        f"train_dataset_path={run_path(train_arg)}",
        f"eval_dataset_path={run_path(val_arg)}",
        f"output_dir={run_path(out_dir)}",
        f"max_seq_len={int(cfg['max_length'])}",
        f"per_device_train_batch_size={int(cfg['batch_size'])}",
        f"gradient_accumulation_steps={int(cfg['grad_accum'])}",
        f"learning_rate={float(cfg['learning_rate']):g}",
        f"num_train_epochs={float(cfg['epochs']):g}",
        f"save_steps={int(cfg['save_steps'])}",
        f"eval_steps={int(cfg['eval_steps'])}",
        "logging_steps=5",
    ]
    if train_type == "lora":
        overrides.append(f"lora_rank={int(cfg['lora_rank'])}")
    if pre_alloc_value:
        overrides.append(f"pre_alloc_memory={int(pre_alloc_value)}")
    command = (
        "paddleformers-cli train \\\n"
        f"  {config_file} \\\n"
        + "".join(f"  {arg} \\\n" for arg in overrides)
    ).rstrip(" \\\n")
    return (
        # PaddleFormers usa tutte le GPU visibili e scarta NPROC_PER_NODE: non
        # va esportato, per non suggerire un parallelismo che non controlla.
        _preamble(cfg, remote=remote, toolchain="paddleformers", workdir=workdir, nproc_env=False)
        + "\n"
        + command
        + "\n"
    )


def generate_script(cfg: dict, run_dir: Path, train_file: Path, val_file: Path) -> str:
    """Script della ricetta dell'adapter, nel toolchain che il vendor documenta."""
    recipe, effective, train_type = resolve_config(cfg)
    run_dir = Path(run_dir)
    if recipe.toolchain == "ms-swift":
        return _ms_swift_script(effective, recipe, run_dir, train_file, val_file)
    if recipe.toolchain == "llama-factory":
        return _llama_factory_script(effective, recipe, run_dir, train_file, val_file)
    if recipe.toolchain == "paddleformers":
        return _paddleformers_script(effective, recipe, run_dir, train_file, val_file)
    raise NotImplementedError(
        f"toolchain '{recipe.toolchain}' non eseguito da questo generatore "
        f"(vista dati '{recipe.data_view}'); doc ufficiale: {recipe.official_doc}"
    )
