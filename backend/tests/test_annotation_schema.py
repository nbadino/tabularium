from app.services.annotation_schema import PageAnnotation, Region, LogicalTable, TableCell, validate_page


def test_schema_accepts_rich_page_and_rejects_duplicate_order():
    page = PageAnnotation(
        page_id=1, width=1000, height=2000, metadata={"issue_no": "42"},
        regions=[
            Region(1, "Text", [[0, 0], [100, 100]], reading_order=0),
            Region(2, "Table", [[100, 0], [500, 300]], reading_order=0),
        ],
        tables=[LogicalTable(2, 2, 2, [TableCell(0, 0, 1, 2, "header")], [])], status="approved",
    )
    errors = validate_page(page)
    assert any("duplicato" in error for error in errors)


def test_schema_detects_merge_outside_grid():
    page = PageAnnotation(
        page_id=1, width=100, height=100, metadata={}, regions=[],
        tables=[LogicalTable(1, 1, 1, [TableCell(0, 0, 2, 1)], [])], status="new",
    )
    assert any("fuori griglia" in error for error in validate_page(page))
