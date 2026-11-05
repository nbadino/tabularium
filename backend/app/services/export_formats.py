"""Exporter indipendenti dal modello (COCO layout e schema canonico JSON)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree

from .annotation_schema import from_records
from .dataset_builder import collect_pages_with_blocks, _project_dir, parse_points


def _bbox(points: list[list[float]]) -> list[float]:
    xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    return [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)]


def export_formats(project_id: int, formats: tuple[str, ...] = ("internal", "coco")) -> dict:
    """Scrive viste derivate senza alterare annotazioni o snapshot esistenti."""
    data = collect_pages_with_blocks(project_id)
    out_dir = _project_dir(project_id) / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if "internal" in formats:
        pages = []
        for item in data.values():
            page = item["page"]
            ann = from_records(page, item["blocks"], item["tables"])
            pages.append(ann.to_dict())
        path = out_dir / "annotations.json"
        path.write_text(json.dumps({"schema_version": "1.0", "pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
        written["internal"] = str(path)
    if "coco" in formats:
        categories: dict[str, int] = {}
        images, annotations = [], []
        ann_id = 1
        for item in data.values():
            page = item["page"]
            images.append({"id": int(page["id"]), "file_name": str(page["abs_path"]), "width": int(page["width"]), "height": int(page["height"])})
            for block in item["blocks"]:
                points = parse_points(block["points"])
                if len(points) < 2:
                    continue
                label = str(block["label"])
                categories.setdefault(label, len(categories) + 1)
                segmentation = [coord for p in points for coord in (float(p[0]), float(p[1]))] if len(points) >= 3 else []
                annotations.append({"id": ann_id, "image_id": int(page["id"]), "category_id": categories[label], "bbox": _bbox(points), "area": _bbox(points)[2] * _bbox(points)[3], "iscrowd": 0, **({"segmentation": [segmentation]} if segmentation else {})})
                ann_id += 1
        path = out_dir / "layout.coco.json"
        path.write_text(json.dumps({"images": images, "annotations": annotations, "categories": [{"id": i, "name": n} for n, i in categories.items()]}, ensure_ascii=False, indent=2), encoding="utf-8")
        written["coco"] = str(path)
    if "html" in formats:
        path = out_dir / "tables.html"
        chunks = ["<!doctype html><html lang='it'><meta charset='utf-8'><body>"]
        for item in data.values():
            for block in item["blocks"]:
                if block["label"] != "Table" or block["id"] not in item["tables"]:
                    continue
                grid = item["tables"][block["id"]]
                chunks.append(f"<table data-page='{item['page']['id']}' data-block='{block['id']}'>")
                cells = {(int(c.get("r", 0)), int(c.get("c", 0))): c for c in grid.get("cells", [])}
                for r in range(int(grid.get("rows", 0))):
                    chunks.append("<tr>")
                    for c in range(int(grid.get("cols", 0))):
                        cell = cells.get((r, c))
                        if not cell:
                            continue
                        attrs = []
                        if int(cell.get("rowspan", 1)) > 1: attrs.append(f"rowspan='{int(cell['rowspan'])}'")
                        if int(cell.get("colspan", 1)) > 1: attrs.append(f"colspan='{int(cell['colspan'])}'")
                        chunks.append(f"<td {' '.join(attrs)}>{escape(str(cell.get('text', '')))}</td>")
                    chunks.append("</tr>")
                chunks.append("</table>")
        chunks.append("</body></html>")
        path.write_text("\n".join(chunks), encoding="utf-8")
        written["html"] = str(path)
    if "alto" in formats:
        root = Element("alto", {"xmlns": "http://www.loc.gov/standards/alto/ns-v4#"})
        layout = SubElement(root, "Layout")
        for number, item in enumerate(data.values(), start=1):
            page = item["page"]
            page_el = SubElement(layout, "Page", {"ID": f"p{page['id']}", "WIDTH": str(int(page["width"])), "HEIGHT": str(int(page["height"])), "PHYSICAL_IMG_NR": str(number)})
            space = SubElement(page_el, "PrintSpace")
            for block in item["blocks"]:
                points = parse_points(block["points"])
                if len(points) < 2:
                    continue
                box = {k: str(int(v)) for k, v in zip(("HPOS", "VPOS", "WIDTH", "HEIGHT"), _bbox(points))}
                region = SubElement(space, "TextBlock", {"ID": f"b{block['id']}", **box})
                if block["content"]:
                    line = SubElement(region, "TextLine", {"ID": f"l{block['id']}", **box})
                    SubElement(line, "String", {"CONTENT": str(block["content"])})
        path = out_dir / "annotations.alto.xml"
        ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        written["alto"] = str(path)
    if "page" in formats:
        # PAGE describes one image per document: one file per page.
        page_dir = out_dir / "page"
        page_dir.mkdir(exist_ok=True)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        for item in data.values():
            page = item["page"]
            root = Element("PcGts", {"xmlns": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"})
            meta = SubElement(root, "Metadata")
            SubElement(meta, "Creator").text = "Tabularium"
            SubElement(meta, "Created").text = now
            SubElement(meta, "LastChange").text = now
            page_el = SubElement(root, "Page", {"imageFilename": str(page.get("rel_path") or Path(str(page["abs_path"])).name), "imageWidth": str(int(page["width"])), "imageHeight": str(int(page["height"]))})
            for block in item["blocks"]:
                points = parse_points(block["points"])
                if len(points) < 2:
                    continue
                if len(points) == 2:
                    (x1, y1), (x2, y2) = points
                    points = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
                region = SubElement(page_el, "TextRegion", {"id": f"b{block['id']}", "custom": f"structure {{type:{block['label']};}}"})
                SubElement(region, "Coords", {"points": " ".join(f"{int(x)},{int(y)}" for x, y in points)})
                if block["content"]:
                    SubElement(SubElement(region, "TextEquiv"), "Unicode").text = str(block["content"])
            ElementTree(root).write(page_dir / f"page_{page['id']}.xml", encoding="utf-8", xml_declaration=True)
        written["page"] = str(page_dir)
    return {"project_id": project_id, "formats": written}
