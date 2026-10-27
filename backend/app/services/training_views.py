"""Viste dati per toolchain di training.

Il dataset canonico di Tabularium (`dataset_builder.py`) è la sola fonte di
verità: una famiglia per file, split per pagina, coordinate 0–1000, tabelle in
OTSL. Ogni toolchain però vuole una forma diversa degli stessi campioni:

| Vista | File | Consumatori |
|---|---|---|
| `ms-swift-jsonl` | `train.jsonl`, `val.jsonl` | MonkeyOCRv2, Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2, dots.ocr, MinerU2.5 |
| `paddleformers-messages` | `train.jsonl`, `val.jsonl` | PaddleOCR-VL (`paddleformers-cli`) |
| `llamafactory-sharegpt` | `train.json`, `val.json`, `dataset_info.json` | GLM-OCR (`llamafactory-cli`) |

La vista **non riscrive il target**: cambia solo il prompt quando il vendor ne
impone uno proprio (GLM-OCR e PaddleOCR-VL pubblicano prompt di training
diversi da quelli di inferenza) e scarta le famiglie che quel modello non sa
produrre (il layout per i modelli senza bbox). Un target mancante o una
famiglia vuota non vengono inventati: il manifest della vista li dichiara e il
preflight li legge.

Per ms-swift e PaddleFormers le immagini restano ai percorsi assoluti dei
ritagli già generati: copiarle per ogni run duplicherebbe gigabyte senza
cambiare un byte. L'eccezione è LLaMA-Factory: la doc GLM-OCR vuole i percorsi
**relativi** alla `dataset_dir` (come `data/` nel repo), quindi la vista GLM
materializza sempre i pixel in `dataset/images/` e scrive `images/<file>`.
Anche il training remoto materializza i pixel, con il prefisso che l'host
vedrà (`images_prefix`), così la run è autosufficiente e l'executor la
sincronizza insieme allo script.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .model_adapters import get_adapter
from .train_recipes import TrainRecipe


@dataclass
class DataView:
    """Esito della materializzazione di una vista."""

    view: str
    train: Path
    val: Path
    files: list[Path] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    prompts_replaced: dict[str, str] = field(default_factory=dict)
    skipped_families: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "view": self.view,
            "train": str(self.train),
            "val": str(self.val),
            "files": [str(path) for path in self.files],
            "counts": self.counts,
            "prompts_replaced": self.prompts_replaced,
            "skipped_families": list(self.skipped_families),
            "warnings": self.warnings,
        }


def _rewrite_prompt(row: dict, prompt: str | None, images: list[str]) -> dict:
    """Sostituisce il solo prompt utente, preservando target e immagini.

    Il numero di `<image>` deve restare uguale a quello di `images` (contratto
    ms-swift, §2.2): si ricostruisce il prefisso dai dati, non si assume che il
    prompt dell'adapter lo contenga già.
    """
    if prompt is None:
        return row
    messages = list(row.get("messages") or [])
    if not messages:
        return row
    messages[0] = {**messages[0], "content": "<image>" * len(images) + prompt}
    return {**row, "messages": messages}


def _read_family(source: Path, family: str, split: str) -> list[dict]:
    path = source / f"{family}_{split}.jsonl"
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _sharegpt_row(row: dict) -> dict:
    """Formato LLaMA-Factory: `messages` con role/content (come la doc GLM)."""
    messages = [
        {"role": item.get("role"), "content": item.get("content", "")}
        for item in row.get("messages") or []
        if item.get("role") in {"user", "assistant", "system"}
    ]
    return {"messages": messages, "images": list(row.get("images") or [])}


_FAMILY_TASK = {
    "layout": "layout",
    "text_rec": "text",
    "table": "table",
    "formula": "formula",
}


def training_prompts(recipe: TrainRecipe) -> dict[str, str]:
    """Prompt con cui *questo* modello vedrà la famiglia a inferenza.

    Il dataset canonico può essere stato costruito con un altro adapter: la
    vista deve riallineare i prompt al modello che si addestra, altrimenti un
    run Monkey addestrato su un dataset costruito per GLM imparerebbe
    "Text Recognition:" e a runtime riceverebbe la frase canonica. La ricetta
    vince sempre; senza override si usano i prompt dell'adapter del modello.
    """
    prompts: dict[str, str] = {}
    if recipe.prompts:
        prompts.update(recipe.prompts)
    try:
        adapter = get_adapter(recipe.adapter_id)
    except ValueError:
        return prompts
    for family, task in _FAMILY_TASK.items():
        if family in prompts:
            continue
        try:
            prompt = adapter.prompt_for(task)
        except (NotImplementedError, ValueError):
            continue
        if prompt:
            prompts[family] = prompt
    return prompts


def _materialize_images(rows: list[dict], destination_dir: Path, prefix: str) -> None:
    """Copia (o linka) i pixel nella run e riscrive i percorsi per l'host remoto.

    Il nome include un hash del percorso sorgente: due ritagli di famiglie
    diverse possono chiamarsi allo stesso modo. `os.link` evita di duplicare i
    byte quando sorgente e destinazione sono sullo stesso filesystem; il
    fallback è una copia. Un'immagine mancante è un errore esplicito: un JSONL
    che punta al nulla addestra fino al primo batch e poi muore.
    """
    images_dir = destination_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        rewritten: list[str] = []
        for value in row.get("images") or []:
            source = Path(value)
            if not source.is_file():
                raise FileNotFoundError(f"immagine del dataset non trovata: {source}")
            digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:12]
            target = images_dir / f"{digest}_{source.name}"
            if not target.exists():
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copy2(source, target)
            rewritten.append(f"{prefix.rstrip('/')}/{target.name}")
        row["images"] = rewritten


def build_view(
    recipe: TrainRecipe,
    source_dir: Path,
    destination_dir: Path,
    *,
    images_prefix: str | None = None,
) -> DataView:
    """Scrive nella forma del toolchain della ricetta i file del run.

    `source_dir` è lo snapshot immutabile del dataset canonico. `destination_dir`
    è la cartella del run: la vista vive lì, così due run non si sovrascrivono
    e il manifest della ricetta può hashare ciò che è stato davvero letto.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    prompts = training_prompts(recipe)
    families = [family for family in recipe.families]
    view = recipe.data_view

    if view in {"ms-swift-jsonl", "paddleformers-messages"}:
        return _messages_view(recipe, source_dir, destination_dir, families, prompts, images_prefix)
    if view == "llamafactory-sharegpt":
        return _sharegpt_view(recipe, source_dir, destination_dir, families, prompts, images_prefix)
    raise ValueError(
        f"vista dati '{view}' sconosciuta per '{recipe.adapter_id}': "
        "una ricetta senza vista non è eseguibile"
    )


def _messages_view(
    recipe: TrainRecipe,
    source_dir: Path,
    destination_dir: Path,
    families: list[str],
    prompts: dict[str, str],
    images_prefix: str | None,
) -> DataView:
    result = DataView(view=recipe.data_view, train=destination_dir / "train.jsonl", val=destination_dir / "val.jsonl")
    counts: dict[str, dict[str, int]] = {}
    replaced: dict[str, str] = {}
    for split in ("train", "val"):
        rows: list[dict] = []
        for family in families:
            family_rows = _read_family(source_dir, family, split)
            counts.setdefault(family, {})[split] = len(family_rows)
            prompt = prompts.get(family)
            if prompt:
                replaced[family] = prompt
            for row in family_rows:
                images = list(row.get("images") or [])
                rows.append(_rewrite_prompt(row, prompt, images))
        if images_prefix:
            _materialize_images(rows, destination_dir, images_prefix)
        target = getattr(result, split)
        with target.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    result.files = [result.train, result.val]
    result.counts = counts
    result.prompts_replaced = replaced
    missing = [family for family in families if not counts.get(family, {}).get("train")]
    if missing:
        result.warnings.append(
            "famiglie vuote nel dataset: " + ", ".join(missing)
        )
    return result


def _sharegpt_view(
    recipe: TrainRecipe,
    source_dir: Path,
    destination_dir: Path,
    families: list[str],
    prompts: dict[str, str],
    images_prefix: str | None,
) -> DataView:
    result = DataView(view=recipe.data_view, train=destination_dir / "train.json", val=destination_dir / "val.json")
    counts: dict[str, dict[str, int]] = {}
    replaced: dict[str, str] = {}
    for split in ("train", "val"):
        rows: list[dict] = []
        for family in families:
            family_rows = _read_family(source_dir, family, split)
            counts.setdefault(family, {})[split] = len(family_rows)
            prompt = prompts.get(family)
            if prompt:
                replaced[family] = prompt
            for row in family_rows:
                images = list(row.get("images") or [])
                rows.append(_sharegpt_row(_rewrite_prompt(row, prompt, images)))
        # GLM-OCR/LLaMA-Factory risolve i percorsi immagine relativi alla
        # `dataset_dir` (la doc ufficiale li vuole relativi a `data/`), non
        # accetta il percorso assoluto dei ritagli canonici. Si materializzano
        # quindi sempre dentro la run e si scrive `images/<file>`.
        _materialize_images(rows, destination_dir, "images")
        getattr(result, split).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    dataset_info = {
        f"tabularium_{recipe.adapter_id}_train": {
            "file_name": result.train.name,
            "formatting": "sharegpt",
            "columns": {"messages": "messages", "images": "images"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
            },
        },
        f"tabularium_{recipe.adapter_id}_val": {
            "file_name": result.val.name,
            "formatting": "sharegpt",
            "columns": {"messages": "messages", "images": "images"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
            },
        },
    }
    info_path = destination_dir / "dataset_info.json"
    info_path.write_text(json.dumps(dataset_info, ensure_ascii=False, indent=2), encoding="utf-8")
    result.files = [result.train, result.val, info_path]
    result.counts = counts
    result.prompts_replaced = replaced
    missing = [family for family in families if not counts.get(family, {}).get("train")]
    if missing:
        result.warnings.append("famiglie vuote nel dataset: " + ", ".join(missing))
    return result
