"""Campionamento deterministico per il pilot iniziale."""
from __future__ import annotations

import json
import random
from collections import defaultdict

from ..adapters import pages as pagestore
from ..adapters import projects as projectstore


def sample_pilot(project_id: int, target: int = 40, seed: int = 42) -> dict:
    target = max(30, min(target, 50))
    rows = sorted(pagestore.all_for_project(project_id), key=lambda row: int(row["id"]))
    protected: set[int] = set()
    try:
        settings = projectstore.settings_json(project_id)
        protected = {
            int(value)
            for value in settings.get("study_protocol", {}).get("gold_pages", [])
        }
    except (TypeError, ValueError):
        pass
    candidates = [row for row in rows if int(row["id"]) not in protected]
    buckets: dict[str, list] = defaultdict(list)
    for row in candidates:
        year = str(row["issue_date"] or "")[:4] or "unknown"
        page_type = str(row["page_type"] or "unclassified")
        buckets[f"{year}:{page_type}"].append(row)
    rng = random.Random(seed)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    selected = []
    # round-robin: evita che un solo anno/tipo domini il pilot.
    keys = sorted(buckets)
    while len(selected) < min(target, len(candidates)) and keys:
        next_keys = []
        for key in keys:
            if buckets[key] and len(selected) < target:
                selected.append(buckets[key].pop())
            if buckets[key]:
                next_keys.append(key)
        keys = next_keys
    return {
        "project_id": project_id,
        "target": target,
        "seed": seed,
        "protected_gold_excluded": len(protected),
        "pages": [{"id": int(row["id"]), "rel_path": row["rel_path"], "issue_date": row["issue_date"], "page_type": row["page_type"]} for row in selected],
    }
