"""Exporter indipendenti dal modello (COCO layout e schema canonico JSON)."""
from __future__ import annotations

import json
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
    for fmt in ("page", "alto"):
        if fmt not in formats:
            continue
        root_tag = "PcGts" if fmt == "page" else "alto"
        root = Element(root_tag, {"xmlns": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"} if fmt == "page" else {"xmlns": "http://www.loc.gov/standards/alto/ns-v4#"})
        if fmt == "page":
            page_el = SubElement(root, "Page", {"imageWidth": "0", "imageHeight": "0"})
        else:
            layout = SubElement(root, "Layout")
        for item in data.values():
            page = item["page"]
            parent = SubElement(page_el if fmt == "page" else layout, "TextRegion", {"id": f"p{page['id']}"})
            for block in item["blocks"]:
                points = parse_points(block["points"])
                if len(points) < 2:
                    continue
                b = _bbox(points)
                attrs = {"id": f"b{block['id']}", "LABEL": str(block["label"])}
                if fmt == "page":
                    region = SubElement(parent, "TextRegion", attrs)
                    SubElement(region, "Coords", {"points": " ".join(f"{int(x)},{int(y)}" for x, y in points)})
                    if block["content"]:
                        line = SubElement(region, "TextLine")
                        SubElement(line, "String", {"CONTENT": str(block["content"])})
                else:
                    region = SubElement(parent, "TextBlock", {"ID": f"b{block['id']}", "HPOS": str(int(b[0])), "VPOS": str(int(b[1])), "WIDTH": str(int(b[2])), "HEIGHT": str(int(b[3]))})
                    if block["content"]:
                        SubElement(region, "TextLine")
        path = out_dir / f"annotations.{fmt}.xml"
        ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        written[fmt] = str(path)
    return {"project_id": project_id, "formats": written}
