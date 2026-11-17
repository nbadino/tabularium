"""Gold-set evaluation of a new checkpoint on the GPU that trained it.

After a one-click training run, and before the merged weights are downloaded and
the rented GPU destroyed, the checkpoint reads the project's gold tables on that
same host (`checkpoint_prepare._prepare_remote`). Without it every comparison of
two versions needed a second rental and hand-run scripts.

What makes two runs' scores comparable is fixed here and recorded in the run:

* **Inputs.** The gold set (`study_protocol.gold_pages`, approved pages only,
  never in a training snapshot) gives the Table blocks with a grid and no
  unverified cell. Each crop is cut by `pages.crop_block_jpeg` from the block's
  bbox, as the dataset builder cuts a training crop, and its SHA-256 is recorded.
* **Prompt and image bounds.** The recipe's table prompt (`_training_prompt`, the
  one the model was trained on) and the recipe's image-size bounds.
* **Decoding.** Greedy, `max_new_tokens` from the recipe (a full register table),
  no repair: an output that hit the token limit, did not parse as a table or
  raised is a failure and scores zero on every reference cell.
* **Environment.** The recipe's `gold_eval` declaration: the image (tag and the
  digest it pointed to), the interpreter and the torch/transformers stack. The
  remote script reports what it actually ran with; a stack that differs from the
  declared one fails the evaluation instead of producing an incomparable score.

The metric is `evaluate.table_metrics` (one owner). A failure of this step never
fails the run: the caller records the error and goes on to download the model.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..domain import otsl as otslmod
from . import dataset_builder as builder
from . import evaluate, train_recipes
from . import pages as pagesvc
from .model_adapters import get_adapter

FAMILY = "table"
DEFAULT_MAX_MINUTES = 60
MAX_MINUTES = (5, 240)
SCRIPT = "gold_infer.py"
# Reasons a run is not evaluated on the gold set (codes; the UI translates them).
NOT_DECLARED = "not_declared"
NO_GOLD_TABLES = "no_gold_tables"
DISABLED = "disabled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp_minutes(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = DEFAULT_MAX_MINUTES
    return max(MAX_MINUTES[0], min(MAX_MINUTES[1], minutes))


def environment(adapter_id: str) -> dict | None:
    """The recipe's declared evaluation environment, or None when it has none."""
    recipe = train_recipes.get_recipe(adapter_id)
    return dict(recipe.gold_eval) if recipe is not None and recipe.gold_eval else None


def gold_tables(project_id: int) -> tuple[list[dict], list[dict]]:
    """The gold Table blocks to read, and the ones left out with the reason.

    Same truth rule as an approved-only dataset build: a table with a cell no
    person verified is not ground truth.
    """
    gold = builder.gold_page_ids(project_id)
    tables: list[dict] = []
    excluded: list[dict] = []
    if not gold:
        return tables, excluded
    for pid, data in sorted(builder.collect_pages_with_blocks(project_id).items()):
        if pid not in gold:
            continue
        page = data["page"]
        if page["status"] not in {"approved", "exported"}:
            excluded.append({"page_id": pid, "reason": "not_approved"})
            continue
        for block in data["blocks"]:
            if block["label"] != "Table":
                continue
            grid = data["tables"].get(block["id"])
            points = builder.parse_points(block["points"])
            if not grid or not grid.get("cells") or not points:
                excluded.append({"page_id": pid, "block_id": block["id"], "reason": "missing_grid"})
                continue
            if any(cell.get("verified") is False for cell in grid["cells"] if (cell.get("text") or "").strip()):
                excluded.append({"page_id": pid, "block_id": block["id"], "reason": "unverified_cells"})
                continue
            tables.append({"page": page, "block_id": int(block["id"]), "points": points, "grid": grid,
                           "label": block["label"]})
    return tables, excluded


def plan(project_id: int, adapter_id: str, *, enabled: bool = True, max_minutes=DEFAULT_MAX_MINUTES) -> dict:
    """Whether a run of this model on this project is evaluated on the gold set."""
    env = environment(adapter_id)
    minutes = clamp_minutes(max_minutes)
    if not enabled:
        return {"state": "skipped", "reason": DISABLED, "max_minutes": minutes}
    if env is None:
        return {"state": "not_applicable", "reason": NOT_DECLARED, "max_minutes": minutes}
    tables, _excluded = gold_tables(project_id)
    if not tables:
        return {"state": "not_applicable", "reason": NO_GOLD_TABLES, "max_minutes": minutes}
    return {
        "state": "planned",
        "max_minutes": minutes,
        "pages": len({int(t["page"]["id"]) for t in tables}),
        "tables": len(tables),
        "image": env["image"],
        "image_digest": env["image_digest"],
    }


def protocol(adapter_id: str) -> dict:
    """The generation settings every gold evaluation of this model uses."""
    env = environment(adapter_id)
    if env is None:
        raise ValueError(f"{adapter_id} declares no gold evaluation environment")
    recipe = train_recipes.get_recipe(adapter_id)
    prompt = builder._training_prompt(recipe, get_adapter(adapter_id), FAMILY, "Table")  # noqa: SLF001
    return {
        "family": FAMILY,
        "prompt": prompt,
        "image_size": dict(env["image_size"]),
        "chat_template_kwargs": dict(env.get("chat_template_kwargs") or {}),
        "decoding": "greedy",
        "max_new_tokens": int(env["max_new_tokens"]),
        "page_timeout_s": int(env["page_timeout_s"]),
    }


def build_bundle(project_id: int, adapter_id: str, run_dir: Path) -> dict:
    """Write the crops and the requests the remote script reads; return the manifest.

    `gold/remote/` is what goes to the GPU (images, `requests.json`, the script);
    `gold/manifest.json` keeps the references here.
    """
    tables, excluded = gold_tables(project_id)
    if not tables:
        raise ValueError("the project has no approved gold page with a verified table")
    root = run_dir / "gold"
    remote = root / "remote"
    shutil.rmtree(root, ignore_errors=True)
    (remote / "images").mkdir(parents=True)
    generation = protocol(adapter_id)
    samples = []
    for table in tables:
        xs = [float(p[0]) for p in table["points"]]
        ys = [float(p[1]) for p in table["points"]]
        bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        data = pagesvc.crop_block_jpeg(table["page"], bbox)
        sample_id = f"p{int(table['page']['id'])}_b{table['block_id']}"
        image = f"images/{sample_id}.jpg"
        (remote / image).write_bytes(data)
        samples.append({
            "id": sample_id,
            "page_id": int(table["page"]["id"]),
            "block_id": table["block_id"],
            "image": image,
            "image_sha256": hashlib.sha256(data).hexdigest(),
            "prompt": generation["prompt"],
            "reference_otsl": otslmod.grid_to_otsl(table["grid"]),
        })
    requests = {"protocol": generation,
                "samples": [{k: s[k] for k in ("id", "image", "image_sha256", "prompt")} for s in samples]}
    (remote / "requests.json").write_text(json.dumps(requests, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copyfile(config.RESOURCE_DIR / "scripts" / "cloud" / SCRIPT, remote / SCRIPT)
    manifest = {"protocol": generation, "samples": samples, "excluded": excluded,
                "requests_sha256": hashlib.sha256((remote / "requests.json").read_bytes()).hexdigest()}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def answer_otsl(text: str) -> str:
    """The OTSL answer, or ValueError. Only its wrapping (empty think, fence) is removed."""
    answer = (text or "").strip()
    if answer.startswith("<think>") and "</think>" in answer:
        answer = answer.split("</think>", 1)[1].strip()
    if answer.startswith("```"):
        answer = answer.split("\n", 1)[1] if "\n" in answer else ""
        answer = answer.rsplit("```", 1)[0].strip()
    if not otslmod.looks_like_otsl(answer):
        raise ValueError("not_otsl")
    grid = otslmod.otsl_to_grid(answer)
    if not grid.get("rows") or not grid.get("cells"):
        raise ValueError("empty_grid")
    return answer


def score_sample(sample: dict, output: dict | None) -> dict:
    """Per-table metrics; any failure scores zero on every reference cell."""
    reference = otslmod.otsl_to_grid(sample["reference_otsl"])
    item = {"id": sample["id"], "page_id": sample["page_id"], "block_id": sample["block_id"],
            "image_sha256": sample["image_sha256"]}
    failure = None
    if output is None:
        failure = "not_run"
    else:
        item.update({k: output.get(k) for k in ("seconds", "generated_tokens", "input_tokens", "eos_reached")})
        if output.get("image_sha256") != sample["image_sha256"]:
            failure = "image_mismatch"
        elif output.get("state") != "finished":
            failure = "execution_failure"
            item["error"] = str(output.get("error") or "")[:500]
        elif output.get("truncated") or not output.get("eos_reached"):
            failure = "truncated"
    answer = ""
    if failure is None:
        try:
            answer = answer_otsl(output.get("prediction") or "")
        except Exception as exc:  # noqa: BLE001
            failure = f"unparsable:{exc}"[:120]
    if failure is None:
        metrics = evaluate.table_metrics(reference, answer, lang="en")
    else:
        empty = {"rows": 0, "cols": 0, "cells": []}
        metrics = {"structure_ok": False, "cell_cer": 1.0, "teds": 0.0,
                   **evaluate.table_cell_metrics(reference, empty)}
        metrics["header_structure_ok"] = False
        item["failure"] = failure
    item["metrics"] = metrics
    return item


def aggregate(items: list[dict]) -> dict:
    """Micro averages over all reference cells and characters; failures included."""
    cells = sum(i["metrics"]["reference_cells"] for i in items)
    exact = sum(i["metrics"]["exact_cells"] for i in items)
    chars = sum(i["metrics"]["reference_characters"] for i in items)
    errors = sum(i["metrics"]["character_errors"] for i in items)
    failures: dict[str, int] = {}
    for item in items:
        if item.get("failure"):
            kind = item["failure"].split(":", 1)[0]
            failures[kind] = failures.get(kind, 0) + 1
    return {
        "tables": len(items),
        "pages": len({i["page_id"] for i in items}),
        "cell_exact": exact / cells if cells else None,
        "exact_cells": exact,
        "reference_cells": cells,
        "cer": min(1.0, errors / chars) if chars else None,
        "character_errors": errors,
        "reference_characters": chars,
        "header_structure_ok": sum(1 for i in items if i["metrics"].get("header_structure_ok")),
        "structure_ok": sum(1 for i in items if i["metrics"].get("structure_ok")),
        "missing_rows": sum(i["metrics"]["missing_rows"] for i in items),
        "extra_rows": sum(i["metrics"]["extra_rows"] for i in items),
        "mean_teds": evaluate._mean([float(i["metrics"].get("teds") or 0.0) for i in items]),  # noqa: SLF001
        "failures": failures,
        "failed": sum(failures.values()),
        "seconds": sum(float(i.get("seconds") or 0.0) for i in items),
    }


def check_environment(declared: dict, measured: dict) -> None:
    """Refuse a score computed with a stack other than the declared one."""
    wrong = [f"{name} {measured.get(name)!r} (declared {value!r})"
             for name, value in (declared.get("stack") or {}).items() if measured.get(name) != value]
    if wrong:
        raise RuntimeError("evaluation environment differs from the declared one: " + "; ".join(wrong))


def score(manifest: dict, outputs: list[dict], measured: dict, declared: dict) -> dict:
    by_id = {o.get("id"): o for o in outputs}
    items = [score_sample(sample, by_id.get(sample["id"])) for sample in manifest["samples"]]
    return {
        "complete": all(i.get("failure") != "not_run" for i in items),
        "aggregate": aggregate(items),
        "pages": items,
        "protocol": manifest["protocol"],
        "requests_sha256": manifest["requests_sha256"],
        "excluded": manifest.get("excluded") or [],
        "environment": {
            "image": declared["image"],
            "image_digest": declared["image_digest"],
            "python": declared["python"],
            "declared_stack": declared.get("stack") or {},
            **{k: v for k, v in measured.items() if k != "pip_freeze"},
        },
    }


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def evaluate_on_remote(*, executor, project_id: int, adapter_id: str, run_dir: Path, remote_run: str,
                       model_remote: str, max_minutes: int, log_file: Path, on_progress=None) -> dict:
    """Run the gold tables through the checkpoint on the training host and score them.

    The remote script stops starting new tables at the time cap; `timeout` kills it
    shortly after, and the tables it did not reach are recorded as `not_run`
    (`complete: false`), so a capped evaluation is never mistaken for a full one.
    """
    declared = environment(adapter_id)
    if declared is None:
        raise ValueError(f"{adapter_id} declares no gold evaluation environment")
    started = _now()
    manifest = build_bundle(project_id, adapter_id, run_dir)
    total = len(manifest["samples"])
    if on_progress:
        on_progress(phase="upload", completed=0, total=total)
    remote_gold = f"{remote_run}/gold"
    executor.upload(run_dir / "gold" / "remote", remote_gold, log_file=log_file)
    budget = clamp_minutes(max_minutes) * 60
    python = shlex.quote(str(declared["python"]))
    command = (
        f"cd {shlex.quote(remote_gold)} && rm -f outputs.jsonl environment.json && "
        f"timeout -k 30 {budget + 60} {python} -u {SCRIPT} --model {shlex.quote(model_remote)} "
        f"--requests requests.json --out outputs.jsonl --environment environment.json --budget-s {budget}"
    )
    process = executor.stream(command, log_file)
    deadline = time.monotonic() + budget + 180
    if on_progress:
        on_progress(phase="inference", completed=0, total=total)
    while process.poll() is None:
        if time.monotonic() > deadline:
            process.terminate()
            break
        time.sleep(15)
        if on_progress:
            try:
                counted = executor.run(f"wc -l < {shlex.quote(remote_gold)}/outputs.jsonl 2>/dev/null || echo 0",
                                       timeout=30)
                done = int((counted.stdout or "0").strip().splitlines()[-1] or 0)
                on_progress(phase="inference", completed=min(done, total), total=total)
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass  # Progress is telemetry: never stop the evaluation for it.
    code = process.wait()
    results = run_dir / "gold" / "results"
    executor.download(remote_gold, results, log_file=log_file)
    try:
        measured = json.loads((results / "environment.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"gold inference exited {code} without reporting its environment") from exc
    if measured.get("error"):
        raise RuntimeError(f"gold inference could not start: {measured['error']}")
    check_environment(declared, measured)
    outputs = _read_jsonl(results / "outputs.jsonl")
    report = score(manifest, outputs, measured, declared)
    report.update(started_at=started, ended_at=_now(), exit_code=code, max_minutes=clamp_minutes(max_minutes),
                  model=model_remote)
    full = dict(report)
    full["outputs"] = outputs
    (run_dir / "gold_eval.json").write_text(json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8")
    report["report"] = "gold_eval.json"
    return report
