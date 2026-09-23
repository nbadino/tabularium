import pytest

from app.services.model_adapters import (
    MonkeyOCRv2ParsingAdapter,
    get_adapter,
    list_adapters,
    supported_prefill_modes,
)
from app.services.prefill import native_mode


def test_monkeyocr_adapter_contract():
    adapter = get_adapter("monkeyocrv2-parsing")
    assert isinstance(adapter, MonkeyOCRv2ParsingAdapter)
    assert adapter.capabilities.coordinate_system == "normalized-0-1000"
    assert adapter.prompt_for("table")
    assert adapter.prompt_for("text", "Column") is None
    assert adapter.serialize_target("formula", "x^2") == "$x^2$"
    assert any(item["adapter_id"] == "monkeyocrv2-parsing" for item in list_adapters())


@pytest.mark.parametrize(
    ("adapter_id", "expected"),
    [
        ("monkeyocrv2-parsing", "two_stage"),
        ("mineru2.5", "two_stage"),
        ("teleocr", "two_stage"),
        ("unlimited-ocr", "end2end"),
        ("dots-ocr", "end2end"),
        ("paddleocr-vl", "official"),
    ],
)
def test_native_prefill_selects_model_workflow(adapter_id, expected):
    adapter = get_adapter(adapter_id)
    assert native_mode(adapter) == expected
    assert supported_prefill_modes(adapter)["supports_native"]


@pytest.mark.parametrize("adapter_id", ["glm-ocr", "deepseek-ocr", "qwen3-vl-8b"])
def test_custom_layout_prompts_are_not_misrepresented_as_native(adapter_id):
    adapter = get_adapter(adapter_id)
    assert supported_prefill_modes(adapter)["supports_native"] is False
    with pytest.raises(ValueError, match="workflow nativo"):
        native_mode(adapter)
