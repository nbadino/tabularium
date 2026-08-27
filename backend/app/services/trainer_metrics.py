"""Telemetria GPU e parsing metriche di training (loss, lr, step)."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"


def gpu_snapshot() -> list[dict]:
    """Snapshot GPU via nvidia-smi (lista vuota se assente)."""
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
        return []
    if out.returncode != 0:
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
    return gpus


def parse_metrics_line(line: str) -> dict | None:
    """Estrae loss/lr/step/epoch da una riga di log ms-swift (formato tollerante)."""
    loss = re.search(rf"""["']?loss["']?\s*[:=]\s*({_NUM})""", line, re.IGNORECASE)
    lr = re.search(
        rf"""["']?(?:lr|learning_rate)["']?\s*[:=]\s*({_NUM})""", line, re.IGNORECASE
    )
    step = re.search(rf"""["']?step["']?\s*[:=]\s*(\d+)""", line, re.IGNORECASE)
    if not (loss or lr):
        return None
    out: dict = {"t": time.time()}
    if loss:
        out["loss"] = float(loss.group(1))
    if lr:
        out["lr"] = float(lr.group(1))
    if step:
        out["step"] = int(step.group(1))
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
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    except (TypeError, ValueError):
        return []
