from pathlib import Path

from app.services.export_formats import _bbox


def test_coco_bbox_uses_source_pixels():
    assert _bbox([[10, 20], [110, 20], [110, 80], [10, 80]]) == [10.0, 20.0, 100.0, 60.0]


def test_page_and_alto_keep_the_text_and_the_page_geometry(monkeypatch, tmp_path):
    from xml.etree import ElementTree as ET

    from app.services import export_formats as ef

    data = {7: {
        "page": {"id": 7, "width": 900, "height": 1300, "abs_path": "/a/p.png", "rel_path": "p.png"},
        "blocks": [{"id": 1, "label": "Text", "points": "[[10,20],[110,80]]", "content": "Abidjan"}],
        "tables": {},
    }}
    monkeypatch.setattr(ef, "collect_pages_with_blocks", lambda _pid: data)
    monkeypatch.setattr(ef, "_project_dir", lambda _pid: tmp_path)
    written = ef.export_formats(1, ("page", "alto"))["formats"]

    alto = ET.parse(written["alto"]).getroot()
    ns = {"a": "http://www.loc.gov/standards/alto/ns-v4#"}
    page = alto.find("a:Layout/a:Page", ns)
    assert (page.get("WIDTH"), page.get("HEIGHT")) == ("900", "1300")
    assert page.find("a:PrintSpace/a:TextBlock/a:TextLine/a:String", ns).get("CONTENT") == "Abidjan"

    pc = ET.parse(Path(written["page"]) / "page_7.xml").getroot()
    ns = {"p": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"}
    page = pc.find("p:Page", ns)
    assert (page.get("imageWidth"), page.get("imageHeight")) == ("900", "1300")
    region = page.find("p:TextRegion", ns)
    assert region.find("p:Coords", ns) is not None
    assert region.find("p:TextEquiv/p:Unicode", ns).text == "Abidjan"
