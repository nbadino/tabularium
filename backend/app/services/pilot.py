"""Campionamento deterministico per il pilot iniziale."""
from __future__ import annotations

import json
import random
from collections import defaultdict

from ..db import connect


def sample_pilot(project_id: int, target: int = 40, seed: int = 42) -> dict:
    target = max(30, min(target, 50))
    with connect() as conn:
        rows = conn.execute("SELECT * FROM pages WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        project = conn.execute("SELECT settings_json FROM projects WHERE id=?", (project_id,)).fetchone()
    protected: set[int] = set()
    if project:
        try:
            protected = {int(v) for v in json.loads(project["settings_json"] or "{}").get("study_protocol", {}).get("gold_pages", [])}
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
