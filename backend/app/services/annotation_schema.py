"""Schema canonico delle annotazioni, indipendente da modello ed exporter.

Il database e questo contratto conservano la forma ricca; adapter ed exporter
producono solo viste derivate (JSONL ms-swift, COCO, PAGE XML, ...).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class Region:
    id: int
    label: str
    geometry: list[list[float]]
    content: str = ""
    reading_order: int | None = None
    kind: str = "rect"
    confirmed: bool = False
    provenance: str | None = None


@dataclass(frozen=True)
class TableCell:
    row: int
    column: int
    rowspan: int = 1
    colspan: int = 1
    text: str = ""


@dataclass(frozen=True)
class LogicalTable:
    region_id: int
    rows: int
    columns: int
    cells: list[TableCell]
    phantom_columns: list[int]
    vertical_lines: list[float] | None = None
    horizontal_lines: list[float] | None = None


@dataclass(frozen=True)
class PageAnnotation:
    page_id: int
    width: int
    height: int
    metadata: dict[str, Any]
    regions: list[Region]
    tables: list[LogicalTable]
    status: str
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_page(annotation: PageAnnotation) -> list[str]:
    """Restituisce errori semantici senza imporre il formato di un modello."""
    errors: list[str] = []
    if annotation.width <= 0 or annotation.height <= 0:
        errors.append("dimensioni pagina non valide")
    for region in annotation.regions:
        if len(region.geometry) < 2:
            errors.append(f"regione {region.id}: geometria insufficiente")
        for point in region.geometry:
            if len(point) < 2 or not all(isinstance(v, (int, float)) for v in point[:2]):
                errors.append(f"regione {region.id}: coordinate non numeriche")
    seen = set()
    for region in annotation.regions:
        if region.reading_order is not None and region.reading_order in seen:
            errors.append(f"ordine di lettura duplicato: {region.reading_order}")
        if region.reading_order is not None:
            seen.add(region.reading_order)
    for table in annotation.tables:
        if table.rows < 1 or table.columns < 1:
            errors.append(f"tabella {table.region_id}: dimensioni non valide")
        for cell in table.cells:
            if cell.row < 0 or cell.column < 0 or cell.rowspan < 1 or cell.colspan < 1:
                errors.append(f"tabella {table.region_id}: cella non valida")
            if cell.row + cell.rowspan > table.rows or cell.column + cell.colspan > table.columns:
                errors.append(f"tabella {table.region_id}: merge fuori griglia")
    return errors


def from_records(page: Any, blocks: list[Any], tables: dict[int, dict]) -> PageAnnotation:
    """Costruisce il contratto canonico da righe SQLite o mapping equivalenti."""
    import json

    def get(record: Any, key: str, default: Any = None) -> Any:
        try:
            return record[key]
        except (KeyError, TypeError, IndexError):
            return default

    regions: list[Region] = []
    logical_tables: list[LogicalTable] = []
    for block in blocks:
        try:
            geometry = json.loads(get(block, "points", "[]") or "[]")
        except (TypeError, ValueError):
            geometry = []
        region = Region(
            id=int(get(block, "id", 0)), label=str(get(block, "label", "")),
            geometry=geometry, content=str(get(block, "content", "") or ""),
            reading_order=get(block, "order_idx"), kind=str(get(block, "kind", "rect")),
            confirmed=bool(get(block, "confirmed", False)), provenance=get(block, "prefill_source"),
        )
        regions.append(region)
        if region.label == "Table" and region.id in tables:
            grid = tables[region.id]
            logical_tables.append(LogicalTable(
                region_id=region.id, rows=int(grid.get("rows", 0)), columns=int(grid.get("cols", 0)),
                cells=[TableCell(int(c.get("r", 0)), int(c.get("c", 0)), int(c.get("rowspan", 1)), int(c.get("colspan", 1)), str(c.get("text", ""))) for c in grid.get("cells", [])],
                phantom_columns=[int(c) for c in grid.get("phantom_cols", [])],
                vertical_lines=[float(c) for c in grid.get("vlines", [])] or None,
                horizontal_lines=[float(c) for c in grid.get("hlines", [])] or None,
            ))
    metadata = {k: page[k] for k in ("issue_date", "issue_no", "page_no", "page_type") if get(page, k) is not None}
    return PageAnnotation(int(get(page, "id", 0)), int(get(page, "width", 0)), int(get(page, "height", 0)), metadata, regions, logical_tables, str(get(page, "status", "new")))
