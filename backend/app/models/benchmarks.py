"""Public benchmark scores, the order of the model catalog.

One benchmark for every model, so the numbers compare: **OmniDocBench v1.6_full,
Overall** (higher is better). The official leaderboard moved from v1.5 to v1.6 on
2026-04-10 (296 harder pages, a new matching method), so v1.5 numbers cannot sit in the
same sort and are not used.

A score comes from the official leaderboard when the model is on it, otherwise from the
vendor's own report, and `reported_by` says which. A model with no published v1.6 score
says so with a reason instead of a guessed number (AGENTS.md R5). Checked on 2026-11-03;
when a vendor or the leaderboard publishes a new value, update it here and in
`tests/test_models_audit.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import NotApplicable

LEADERBOARD = "https://github.com/opendatalab/OmniDocBench"
BENCHMARK = "OmniDocBench v1.6"
METRIC = "Overall"


@dataclass(frozen=True)
class PublicScore:
    value: float
    # "leaderboard" (opendatalab's official table) or "vendor" (the model's own report).
    reported_by: str
    source: str
    note: str = ""


PUBLIC_SCORES: dict[str, PublicScore | NotApplicable] = {
    "teleocr": PublicScore(96.91, "leaderboard", LEADERBOARD),
    "xiaomi-ocr-0": PublicScore(
        96.83, "vendor", "https://huggingface.co/xiaomi-research/Xiaomi-OCR-0",
        note="not on the leaderboard",
    ),
    "paddleocr-vl": PublicScore(96.34, "leaderboard", LEADERBOARD),
    "mineru2.5": PublicScore(
        95.75, "leaderboard", LEADERBOARD,
        note="leaderboard row MinerU2.5-Pro; the vendor reports 95.72 for Pro-2605",
    ),
    "glm-ocr": PublicScore(95.22, "leaderboard", LEADERBOARD),
    "unlimited-ocr-2": PublicScore(
        94.02, "vendor", "https://arxiv.org/abs/2610.39308",
        note="technical report, not on the leaderboard",
    ),
    "monkeyocrv2-parsing": PublicScore(
        91.80, "leaderboard", LEADERBOARD,
        note="leaderboard row MonkeyOCR V2, which does not name the S or B Parsing variant",
    ),
    "deepseek-ocr": PublicScore(90.25, "leaderboard", LEADERBOARD),
    "dots-ocr": NotApplicable(
        "dots.mocr publishes OmniDocBench v1.5 edit distance only; the leaderboard's "
        "dots.ocr rows are the older model"
    ),
    "qwen3-vl-8b": NotApplicable(
        "no machine-readable 8B score; the leaderboard lists only Qwen3-VL-235B"
    ),
    "lightonocr-3": NotApplicable("LightOn reports olmOCR-Bench, not OmniDocBench"),
    "surya-ocr-2.5": NotApplicable("Datalab reports its own OmniParseBench, not OmniDocBench"),
    "gemini-argon": NotApplicable(
        "hosted model without OCR benchmarks in its card; the leaderboard lists only Gemini 3"
    ),
    "llamaindex-ocria-1": NotApplicable("hosted parser without a published OmniDocBench score"),
}


def public_benchmark(adapter_id: str) -> dict:
    """The catalog's view of a model's score: a value with its provenance, or the reason
    there is none. A model registered by the user (custom checkpoint) has no entry."""
    entry = PUBLIC_SCORES.get(adapter_id)
    base = {"benchmark": BENCHMARK, "metric": METRIC}
    if isinstance(entry, PublicScore):
        return {
            **base,
            "value": entry.value,
            "reported_by": entry.reported_by,
            "source": entry.source,
            "note": entry.note,
        }
    reason = entry.reason if isinstance(entry, NotApplicable) else "custom checkpoint"
    return {**base, "value": None, "reported_by": None, "source": None, "note": reason}
