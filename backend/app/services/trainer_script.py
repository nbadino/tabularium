"""Generazione script di training ms-swift e preparazione file dataset."""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .dataset_builder import _project_dir

DEFAULTS: dict = {
    "model": "zenosai/MonkeyOCRv2-B-Parsing",
    "model_path": "",
    "train_type": "lora",
    "lora_rank": 8,
    "lora_alpha": 32,
    "freeze_vit": True,
    "epochs": 1.0,
    "learning_rate": 1e-5,
    "batch_size": 4,
    "grad_accum": 1,
    "max_length": 16384,
    "max_pixels": 1003520,
    "gpus": "0",
    "nproc": 1,
    "eval_steps": 200,
}

FAMILIES = ("layout", "text_rec", "table", "formula")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def prepare_training_files(
    project_id: int, destination_dir: Path | None = None
) -> tuple[Path, Path]:
    """Unisce le famiglie in file stabili per uno specifico run."""
    source, _snapshot_id = _dataset_source(project_id)
    target = destination_dir or (_project_dir(project_id) / "dataset")
    target.mkdir(parents=True, exist_ok=True)

    def merge(suffix: str) -> Path:
        chunks = []
        for fam in FAMILIES:
            f = source / f"{fam}_{suffix}.jsonl"
            if f.exists():
                chunks.append(f.read_text(encoding="utf-8"))
        out = target / f"{suffix}.jsonl"
        staging = target / f".{suffix}.jsonl.tmp"
        staging.write_text("".join(chunks), encoding="utf-8")
        staging.replace(out)
        return out

    return merge("train"), merge("val")


def cd_line(repo_train_dir: str) -> str:
    if repo_train_dir:
        return f'cd "{repo_train_dir}"\n'
    return "# (TABULARIUM_TRAIN_REPO non configurato: lo script gira dalla cartella corrente)\n"


def generate_script(cfg: dict, run_dir: Path, train_file: Path, val_file: Path) -> str:
    c = {**DEFAULTS, **cfg}
    model_ref = c["model_path"].strip() or c["model"]
    train_type = c["train_type"]
    out_dir = c.get("output_dir") or str(run_dir / "checkpoints" / f"monkeyocrv2_{train_type}")
    repo_train = Path(config.TRAIN_REPO) / "parsing" / "train"
    repo_train_dir = str(repo_train) if repo_train.is_dir() else ""

    if train_type == "lora":
        lora_flags = (
            f"  --lora_rank {c['lora_rank']} \\\n"
            f"  --lora_alpha {c['lora_alpha']} \\\n"
            '  --target_modules all-linear \\\n'
        )
    else:
        lora_flags = ""

    python_bootstrap = ""
    if config.TRAIN_PYTHON:
        py = shlex.quote(config.TRAIN_PYTHON)
        python_bootstrap = (
            f'export PATH="$(dirname {py}):$PATH"\n'
        )
    else:
        python_bootstrap = (
            'if command -v conda >/dev/null 2>&1; then\n'
            '  source "$(conda info --base)/etc/profile.d/conda.sh"\n'
            f'  conda activate {config.TRAIN_ENV}\n'
            'fi\n'
        )

    script = f"""#!/usr/bin/env bash
set -euo pipefail

export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
export CUDA_VISIBLE_DEVICES="{c['gpus']}"
export NPROC_PER_NODE={c['nproc']}
export MODELSCOPE_CACHE="{run_dir / 'cache'}"

{python_bootstrap}{cd_line(repo_train_dir)}
# flash-attn se disponibile, altrimenti attn_impl=eager (più lento ma funziona)
ATTN="flash_attention_2"
if ! python -c "import flash_attn" >/dev/null 2>&1; then
  echo ">> flash-attn non disponibile: uso attn_impl=eager"
  ATTN="eager"
fi

output_dir="{out_dir}"
resume_options=""
if [ -d "$output_dir" ]; then
  latest=$(ls "$output_dir" 2>/dev/null | grep -E '^checkpoint-[0-9]+$' | sort -t- -k2 -n | tail -1 || true)
  if [ -n "$latest" ]; then
    resume_options="--resume_from_checkpoint $output_dir/$latest"
  fi
fi

swift sft \\
  --model "{model_ref}" \\
  --model_type monkeyocrv2 \\
  --template monkeyocrv2 \\
  --attn_impl "$ATTN" \\
  --dataset "{train_file}" \\
  --val_dataset "{val_file}" \\
  --load_from_cache_file True \\
  --dataloader_num_workers 4 \\
  --dataset_num_proc 4 \\
  --dataset_shuffle True \\
  --streaming False \\
  --max_length {c['max_length']} \\
  --truncation_strategy right \\
  --max_pixels {c['max_pixels']} \\
  --padding_free True \\
  --train_type '{train_type}' \\
{lora_flags}  --freeze_vit {'true' if c['freeze_vit'] else 'false'} \\
  --freeze_aligner false \\
  --freeze_llm false \\
  --torch_dtype bfloat16 \\
  --deepspeed zero1 \\
  --gradient_checkpointing True \\
  --per_device_train_batch_size {c['batch_size']} \\
  --per_device_eval_batch_size 2 \\
  --gradient_accumulation_steps {c['grad_accum']} \\
  --num_train_epochs {c['epochs']} \\
  --learning_rate {c['learning_rate']:g} \\
  --warmup_ratio 0.05 \\
  --lr_scheduler_type cosine \\
  --eval_steps {c['eval_steps']} \\
  --save_steps 500 \\
  --save_total_limit 2 \\
  --logging_steps 5 \\
  --no_add_version \\
  --output_dir "{out_dir}" \\
  $resume_options
"""
    return script
