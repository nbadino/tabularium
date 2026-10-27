"""Custom model rows (P7.1 of `docs/architecture/program.md`)."""
from __future__ import annotations

from ..db import connect


def clear_source_path(adapter_id: str) -> None:
    """Forget where a custom model came from, after it has been imported."""
    with connect() as conn:
        conn.execute(
            "UPDATE custom_models SET source_path=NULL WHERE id=?", (adapter_id,)
        )
