from app.services.table_provenance import with_cell_provenance


def test_legacy_model_cells_are_not_human_verified():
    original = {"cells": [{"r": 0, "c": 0, "text": "Achilles"}]}
    result = with_cell_provenance(original, "model:mineru:official")
    assert result["cells"][0]["source"] == "model"
    assert result["cells"][0]["verified"] is False
    assert "verified" not in original["cells"][0]


def test_existing_human_verification_is_preserved():
    grid = {"cells": [
        {"text": "Achilles", "source": "manual", "verified": True},
        {"text": "Br", "source": "model", "verified": True},
        {"text": "98", "source": "manual", "verified": False},
    ]}
    assert with_cell_provenance(grid, "model:mineru")["cells"] == grid["cells"]
    assert with_cell_provenance(grid, None) is grid


def test_fresh_model_results_always_start_unverified():
    grid = {"cells": [{"text": "Achilles", "source": "manual", "verified": True}]}
    result = with_cell_provenance(grid, "model:mineru", generated=True)
    assert result["cells"][0]["verified"] is False
    assert result["cells"][0]["source"] == "model"
