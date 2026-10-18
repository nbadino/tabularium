"""Le viste dati per toolchain: stesso gold, forma diversa (M18).

Il punto di questi test non è il file: è che **il target non cambi**. Cambia il
prompt quando il vendor ne impone uno proprio, cambiano i contenitori
(`messages` vs `conversations`, JSONL vs JSON), ma il contenuto trascritto e
l'immagine devono essere gli stessi. Una vista che riscrive il target è un
dataset diverso travestito.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services import train_recipes
from app.services.training_views import build_view


def _canonical_dataset(root: Path) -> Path:
    """Un dataset canonico minimo: una famiglia per file, split train/val."""
    root.mkdir(parents=True, exist_ok=True)
    rows = {
        "layout": {
            "images": ["/data/p1.png"],
            "messages": [
                {"role": "user", "content": "<image>Please output the categories and coordinates of the document elements in reading order."},
                {"role": "assistant", "content": "[{\"bbox\":[1,2,3,4],\"label\":\"Text\"}]"},
            ],
        },
        "text_rec": {
            "images": ["/data/crop-text.png"],
            "messages": [
                {"role": "user", "content": "<image>Please output the text content from the image."},
                {"role": "assistant", "content": "Abava (m. tank) Ru 71"},
            ],
        },
        "table": {
            "images": ["/data/crop-table.png"],
            "messages": [
                {"role": "user", "content": "<image>Please extract the table from the image and represent it in OTSL format."},
                {"role": "assistant", "content": "<fcel>Abava<fcel>Ru<nl><fcel>71<ecel><nl>"},
            ],
        },
        "formula": {
            "images": ["/data/crop-formula.png"],
            "messages": [
                {"role": "user", "content": "<image>Please write out the expression of the formula in the image using LaTeX format."},
                {"role": "assistant", "content": "$C_{0}$"},
            ],
        },
    }
    for family, row in rows.items():
        for split in ("train", "val"):
            (root / f"{family}_{split}.jsonl").write_text(
                json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
            )
    return root


def test_llamafactory_view_uses_official_glm_prompts_and_drops_layout(tmp_path: Path):
    recipe = train_recipes.get_recipe("glm-ocr")
    assert recipe is not None
    source = _canonical_dataset(tmp_path / "canonical")
    destination = tmp_path / "view"
    view = build_view(recipe, source, destination)

    rows = json.loads(view.train.read_text(encoding="utf-8"))
    assert rows, "la vista GLM non deve essere vuota"
    # Niente layout: GLM-OCR non produce bbox, addestrarlo sul layout
    # insegnerebbe un output che il suo protocollo non ha.
    assert all("coordinates" not in row["messages"][0]["content"] for row in rows)
    prompts = {row["messages"][0]["content"] for row in rows}
    assert "<image>Text Recognition:" in prompts
    assert "<image>Table Recognition:" in prompts
    assert "<image>Formula Recognition:" in prompts
    # Il target resta quello canonico, non viene rigenerato.
    table = next(row for row in rows if "Table Recognition" in row["messages"][0]["content"])
    assert table["messages"][1]["content"].startswith("<fcel>Abava")
    assert table["images"] == ["/data/crop-table.png"]
    # Il dataset_info è quello che LLaMA-Factory legge dal dataset_dir.
    info = json.loads((destination / "dataset_info.json").read_text(encoding="utf-8"))
    entry = info["tabularium_glm-ocr_train"]
    assert entry["file_name"] == "train.json"
    assert entry["formatting"] == "sharegpt"
    assert entry["tags"]["user_tag"] == "user"
    assert "layout" not in view.counts, "il layout non entra nel dataset GLM"
    assert view.counts["table"]["train"] == 1


def test_paddleformers_view_uses_official_prompts_in_messages_shape(tmp_path: Path):
    recipe = train_recipes.get_recipe("paddleocr-vl")
    assert recipe is not None
    source = _canonical_dataset(tmp_path / "canonical")
    view = build_view(recipe, source, tmp_path / "view")
    rows = [json.loads(line) for line in view.train.read_text(encoding="utf-8").splitlines()]
    prompts = {row["messages"][0]["content"] for row in rows}
    assert "<image>OCR:" in prompts
    assert "<image>Table Recognition:" in prompts
    # La forma è quella di ms-swift/PaddleFormers: messages + images.
    assert all(set(row) == {"messages", "images"} for row in rows)
    assert all(row["messages"][0]["role"] == "user" for row in rows)
    assert "layout" not in view.counts


def test_msswift_view_keeps_the_adapter_prompt(tmp_path: Path):
    recipe = train_recipes.get_recipe("monkeyocrv2-parsing")
    assert recipe is not None
    source = _canonical_dataset(tmp_path / "canonical")
    view = build_view(recipe, source, tmp_path / "view")
    rows = [json.loads(line) for line in view.train.read_text(encoding="utf-8").splitlines()]
    # Quattro famiglie × train: il layout resta, il prompt non è riscritto.
    assert view.counts == {family: {"train": 1, "val": 1} for family in ("layout", "text_rec", "table", "formula")}
    assert any("represent it in OTSL format" in row["messages"][0]["content"] for row in rows)
    # La vista riscrive il prompt anche quando coincide: così un dataset
    # costruito per un altro modello non porta i suoi prompt in questo run.
    assert "layout" in view.prompts_replaced


def test_view_realigns_prompts_to_the_trained_model(tmp_path: Path):
    """Dataset costruito per GLM, training Monkey: i prompt tornano canonici."""
    source = _canonical_dataset(tmp_path / "canonical")
    # Simula una build fatta con l'adapter GLM.
    for path in source.glob("*.jsonl"):
        row = json.loads(path.read_text(encoding="utf-8"))
        row["messages"][0]["content"] = "<image>Text Recognition:"
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    view = build_view(train_recipes.get_recipe("monkeyocrv2-parsing"), source, tmp_path / "view")
    rows = [json.loads(line) for line in view.train.read_text(encoding="utf-8").splitlines()]
    assert all("Text Recognition:" not in row["messages"][0]["content"] for row in rows)
    assert any(row["messages"][0]["content"].startswith("<image>Please output the text") for row in rows)


def test_missing_family_is_declared_not_invented(tmp_path: Path):
    recipe = train_recipes.get_recipe("glm-ocr")
    assert recipe is not None
    source = _canonical_dataset(tmp_path / "canonical")
    (source / "table_train.jsonl").unlink()
    view = build_view(recipe, source, tmp_path / "view")
    assert view.counts["table"]["train"] == 0
    assert any("table" in warning for warning in view.warnings)
