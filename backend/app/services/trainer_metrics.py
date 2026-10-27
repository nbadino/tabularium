"""Telemetria GPU e parsing metriche di training (loss, lr, step)."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_GPU_CACHE_TTL = 10.0
_gpu_cache: tuple[float, list[dict]] | None = None
_gpu_cache_lock = threading.Lock()


def gpu_snapshot() -> list[dict]:
    """Snapshot GPU via nvidia-smi (lista vuota se assente)."""
    global _gpu_cache
    now = time.monotonic()
    with _gpu_cache_lock:
        if _gpu_cache is not None and now - _gpu_cache[0] < _GPU_CACHE_TTL:
            return [dict(gpu) for gpu in _gpu_cache[1]]
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:  # noqa: BLE001
        with _gpu_cache_lock:
            _gpu_cache = (now, [])
        return []
    if out.returncode != 0:
        with _gpu_cache_lock:
            _gpu_cache = (now, [])
        return []
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6:
            gpus.append(
                {
                    "index": parts[0],
                    "name": parts[1],
                    "memory_total": int(float(parts[2])),
                    "memory_used": int(float(parts[3])),
                    "utilization": int(float(parts[4])),
                    "temp": int(float(parts[5])),
                }
            )
    with _gpu_cache_lock:
        _gpu_cache = (time.monotonic(), gpus)
    return [dict(gpu) for gpu in gpus]


def parse_metrics_line(line: str) -> dict | None:
    """Estrae loss/lr/step/epoch da una riga di log ms-swift (formato tollerante)."""
    loss = re.search(rf"""(?<![\w])["']?loss["']?\s*[:=]\s*({_NUM})""", line, re.IGNORECASE)
    lr = re.search(
        rf"""["']?(?:lr|learning_rate)["']?\s*[:=]\s*({_NUM})""", line, re.IGNORECASE
    )
    step = re.search(rf"""["']?step["']?\s*[:=]\s*(\d+)""", line, re.IGNORECASE)
    progress = re.search(r"global_step/max_steps['\"]?\s*:\s*['\"](\d+)/(\d+)", line)
    if not (loss or lr or progress):
        return None
    out: dict = {"t": time.time()}
    if loss:
        out["loss"] = float(loss.group(1))
    if lr:
        out["lr"] = float(lr.group(1))
    if step:
        out["step"] = int(step.group(1))
    if progress:
        out["step"], out["total_steps"] = map(int, progress.groups())
    for key in ("epoch", "eval_loss"):
        match = re.search(rf"[\"']?{key}[\"']?\s*:\s*({_NUM})", line)
        if match:
            out[key] = float(match.group(1))
    remaining = re.search(r"remaining_time['\"]?\s*:\s*['\"]([^'\"]+)", line)
    if remaining:
        out["remaining_time"] = remaining.group(1)
    return out


def _log_tail(log_file: Path, n: int = 30) -> str:
    if not log_file.exists():
        return ""
    try:
        with log_file.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 20000))
            return fh.read().decode("utf-8", errors="replace")[-20000:]
    except OSError:
        return ""


def _log_tail_logfile(run_dir: Path) -> list[str]:
    log = run_dir / "train.log"
    if not log.exists():
        return []
    try:
        return log.read_text(encoding="utf-8", errors="replace").splitlines()[-20000:]
    except OSError:
        return []


def _extract_metrics(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        m = parse_metrics_line(line)
        if m:
            out.append(m)
    return out[-500:]


def _metrics(run_dir: Path) -> list[dict]:
    f = run_dir / "metrics.jsonl"
    if not f.exists():
        lines = _log_tail_logfile(run_dir)
        return _extract_metrics(lines)
    import json
    try:
        metrics = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if metrics and not any(item.get("step") is not None for item in metrics):
            parsed = _extract_metrics(_log_tail_logfile(run_dir))
            if any(item.get("step") is not None for item in parsed):
                return parsed
        return metrics or _extract_metrics(_log_tail_logfile(run_dir))
    except (TypeError, ValueError):
        return []


def summarize_log(
    lines: list[str],
    *,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> dict:
    """Riepilogo finale di una run, dai log dei tre toolchain.

    Non è una metrica di qualità: sono i numeri che il trainer stampa alla fine
    (train/eval loss, step, durata, picco di memoria). La UI li mostra accanto
    al checkpoint, così una run non finisce in un log da leggere a mano.
    """
    import datetime as _dt

    text = "\n".join(lines)
    patterns = {
        "train_loss": rf"\btrain_loss\b[\"']?\s*[:=]\s*[\"']?({_NUM})",
        "eval_loss": rf"\beval_loss\b[\"']?\s*[:=]\s*[\"']?({_NUM})",
        "eval_token_acc": rf"\beval_token_acc\b[\"']?\s*[:=]\s*[\"']?({_NUM})",
        "train_runtime_s": rf"\btrain_runtime\b[\"']?\s*[:=]\s*[\"']?({_NUM})",
        "peak_gib": rf"\bmemory\s*\(\s*GiB\s*\)[\"']?\s*[:=]\s*[\"']?({_NUM})",
        "max_memory_gib": rf"\bmax_memory_allocated\b[\"']?\s*[:=]\s*[\"']?({_NUM})",
    }
    out: dict = {}
    for key, pattern in patterns.items():
        found = re.findall(pattern, text, re.IGNORECASE)
        if found:
            try:
                out[key] = float(found[-1])
            except (TypeError, ValueError):
                continue
    steps = re.findall(
        rf"global_step\s*/\s*max_steps[\"']?\s*[:=]\s*[\"']?(\d+)\s*/\s*(\d+)",
        text,
        re.IGNORECASE,
    )
    if steps:
        out["steps"] = int(steps[-1][0])
        out["total_steps"] = int(steps[-1][1])
    else:
        last_step = re.findall(rf"[\"']?\bstep\b[\"']?\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        if last_step:
            out["steps"] = int(last_step[-1])
        else:
            # PaddleFormers stampa "global_step: N" senza il totale.
            paddle_steps = re.findall(r"global_step\s*[:=]\s*(\d+)", text, re.IGNORECASE)
            if paddle_steps:
                out["steps"] = int(paddle_steps[-1])
    losses = re.findall(rf"[\"']?\bloss\b[\"']?\s*[:=]\s*[\"']?({_NUM})", text, re.IGNORECASE)
    if losses:
        out["last_loss"] = float(losses[-1])
    if "peak_gib" not in out and "max_memory_gib" in out:
        out["peak_gib"] = out["max_memory_gib"]
    if "train_runtime_s" not in out and started_at and ended_at:
        try:
            start = _dt.datetime.fromisoformat(started_at)
            end = _dt.datetime.fromisoformat(ended_at)
            out["duration_s"] = max(0.0, (end - start).total_seconds())
        except (TypeError, ValueError):
            pass
    return out
