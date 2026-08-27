from app.services.export_formats import _bbox


def test_coco_bbox_uses_source_pixels():
    assert _bbox([[10, 20], [110, 20], [110, 80], [10, 80]]) == [10.0, 20.0, 100.0, 60.0]
