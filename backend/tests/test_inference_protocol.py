"""The protocol halves that need no socket: request shape, answer meaning."""
from __future__ import annotations

import json
from pathlib import Path

from app.domain import inference as protocol


def test_google_request_puts_the_image_before_the_prompt() -> None:
    body = protocol.google_request("leggi la pagina", "QUJD", max_tokens=1234)
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"inline_data": {"mime_type": "image/png", "data": "QUJD"}}
    assert parts[1] == {"text": "leggi la pagina"}
    assert body["generationConfig"]["maxOutputTokens"] == 1234


def test_google_request_translates_sampling_names() -> None:
    # The panel speaks the OpenAI vocabulary; Google expects temperature/topP/topK.
    body = protocol.google_request(
        "x", "y", sampling={"temperature": 0.2, "top_p": 0.9, "top_k": 5, "max_tokens": 99}
    )
    generation = body["generationConfig"]
    assert generation["temperature"] == 0.2
    assert generation["topP"] == 0.9
    assert generation["topK"] == 5
    # max_tokens is the output budget, not a generation knob: it comes from the argument.
    assert "max_tokens" not in generation


def test_google_request_ignores_unset_and_zero_values() -> None:
    body = protocol.google_request("x", "y", sampling={"temperature": 0.0, "top_p": None})
    assert body["generationConfig"]["temperature"] == 0.0
    assert "topP" not in body["generationConfig"]


def test_google_response_joins_every_part_and_reports_the_finish_reason() -> None:
    text, reason = protocol.google_response({
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": "abc"}, {"text": "def"}, {"inlineData": {}}]},
        }],
    })
    assert text == "abcdef"
    assert reason == "STOP"


def test_google_response_tolerates_an_empty_answer() -> None:
    # Not an error: the layout path retries, the table path falls back to the crop.
    assert protocol.google_response({}) == ("", None)
    assert protocol.google_response({"candidates": []}) == ("", None)


def test_google_usage_reads_both_token_counts() -> None:
    assert protocol.google_usage({
        "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7},
    }) == {"prompt_tokens": 11, "completion_tokens": 7}
    assert protocol.google_usage({}) == {"prompt_tokens": None, "completion_tokens": None}


def test_llamaindex_base_url_accepts_any_endpoint_the_user_pastes() -> None:
    assert protocol.llamaindex_base_url("https://api.cloud.llamaindex.ai/") == \
        "https://api.cloud.llamaindex.ai"
    assert protocol.llamaindex_base_url(
        "https://api.cloud.llamaindex.ai/api/v2/parse"
    ) == "https://api.cloud.llamaindex.ai"
    assert protocol.llamaindex_base_url("https://x/api/v1/beta/files") == "https://x"
    assert protocol.llamaindex_base_url(None) == protocol.LLAMAINDEX_BASE_URL


def test_llamaindex_pages_accepts_every_shape_the_api_returns() -> None:
    assert protocol.llamaindex_pages({"items": {"pages": [1]}}) == [1]
    assert protocol.llamaindex_pages({"job": {"items": {"pages": [2]}}}) == [2]
    assert protocol.llamaindex_pages({"pages": [3]}) == [3]
    assert protocol.llamaindex_pages({}) == []


def test_llamaindex_versions_collects_names_without_duplicates() -> None:
    payload = {
        "versions": [
            {"version": "2024-01-01"},
            {"name": "agentic"},
            {"id": "2024-01-01"},
            {"nested": {"version": "fast"}},
        ]
    }
    assert protocol.llamaindex_versions(payload) == ["2024-01-01", "agentic", "fast"]
    assert protocol.llamaindex_versions({}) == []


def test_llamaindex_configuration_keeps_tables_html() -> None:
    # Tabularium reads table HTML to preserve merged cells; Markdown tables
    # would destroy the structure the annotation grid depends on.
    payload, _ = protocol.llamaindex_configuration({})
    tables = payload["output_options"]["markdown"]["tables"]
    assert tables["output_tables_as_markdown"] is False
    assert payload["client_name"] == "tabularium"
    assert payload["tier"] == "agentic"
    assert payload["processing_options"]["ocr_parameters"]["languages"] == ["eng"]


def test_llamaindex_configuration_derives_the_job_timeout() -> None:
    payload, total = protocol.llamaindex_configuration({
        "job_timeout_in_seconds": 120,
        "job_timeout_extra_time_per_page_in_seconds": 10,
    })
    assert payload["processing_control"]["timeouts"]["base_in_seconds"] == 120
    assert total == 120 + 10 + 60


def test_llamaindex_configuration_only_sends_optional_blocks_when_enabled() -> None:
    plain, _ = protocol.llamaindex_configuration({})
    assert "crop_box" not in plain
    assert "agentic_options" not in plain
    assert "input_options" not in plain

    enabled, _ = protocol.llamaindex_configuration({
        "crop_enabled": True,
        "crop_box": {"top": 0.1, "bottom": 0.2, "left": 0.0, "right": 0.0},
        "custom_prompt": "  conserva le colonne  ",
        "camera_photo_correction": True,
    })
    assert enabled["crop_box"] == {"top": 0.1, "bottom": 0.2, "left": 0.0, "right": 0.0}
    assert enabled["agentic_options"] == {"custom_prompt": "conserva le colonne"}
    assert enabled["input_options"] == {"image": {"camera_photo_correction": True}}


def test_llamaindex_configuration_cost_optimizer_needs_the_agentic_tier() -> None:
    cheap, _ = protocol.llamaindex_configuration({"tier": "cost_effective", "cost_optimizer": True})
    assert "cost_optimizer" not in cheap["processing_options"]

    agentic, _ = protocol.llamaindex_configuration({"tier": "agentic", "cost_optimizer": True})
    assert agentic["processing_options"]["cost_optimizer"] == {"enable": True}


def test_openai_content_honours_the_declared_order() -> None:
    image_first = protocol.openai_content("data:image/png;base64,AA", "leggi")
    assert [part["type"] for part in image_first] == ["image_url", "text"]

    text_first = protocol.openai_content("data:image/png;base64,AA", "leggi", text_first=True)
    assert [part["type"] for part in text_first] == ["text", "image_url"]

    prefixed = protocol.openai_content("u", "leggi", prompt_prefix="<|img|>")
    assert prefixed[1]["text"] == "<|img|>leggi"


def test_openai_request_puts_the_system_prompt_first() -> None:
    body = protocol.openai_request(
        "m", [{"type": "text", "text": "x"}], max_tokens=10, stream=False,
        system_prompt="sei un OCR",
    )
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert "stream_options" not in body

    no_system = protocol.openai_request(
        "m", [], max_tokens=10, stream=True,
    )
    assert [message["role"] for message in no_system["messages"]] == ["user"]
    assert no_system["stream_options"] == {"include_usage": True}


def test_openai_request_routes_the_ngram_knob_where_the_vendor_expects_it() -> None:
    # vLLM takes it as an extra argument; MinerU2.5's logits processor reads vllm_xargs.
    generic = protocol.openai_request(
        "m", [], max_tokens=1, stream=False, sampling={"no_repeat_ngram_size": 30},
    )
    assert generic["extra_args"] == {"no_repeat_ngram_size": 30}
    assert "no_repeat_ngram_size" not in generic

    mineru = protocol.openai_request(
        "m", [], max_tokens=1, stream=False,
        sampling={"no_repeat_ngram_size": 30}, adapter_id="mineru2.5",
    )
    assert mineru["vllm_xargs"] == {"no_repeat_ngram_size": 30}
    assert "extra_args" not in mineru


def test_openai_request_does_not_mutate_the_sampling_it_receives() -> None:
    sampling = {"temperature": 0.1, "no_repeat_ngram_size": 30}
    body = protocol.openai_request("m", [], max_tokens=1, stream=False, sampling=sampling)
    assert body["temperature"] == 0.1
    assert sampling == {"temperature": 0.1, "no_repeat_ngram_size": 30}


# The vendor's request schema, fetched from LlamaCloud's OpenAPI and stored next to
# the tests (R3): every sub-object is `additionalProperties: false`, so a renamed key
# is a rejected job, not an ignored option.
_LLAMA_SCHEMA = json.loads(
    (Path(__file__).parent / "fixtures" / "llamacloud_parse_v2_schema.json").read_text()
)


def _llama_violations(value, node, path: str = "") -> list[str]:
    schemas = _LLAMA_SCHEMA["schemas"]
    if "$ref" in node:
        return _llama_violations(value, schemas[node["$ref"].split("/")[-1]], path)
    alternatives = node.get("anyOf")
    if alternatives:
        results = [_llama_violations(value, alt, path) for alt in alternatives]
        return [] if any(not r for r in results) else min(results, key=len)
    if isinstance(value, dict):
        if node.get("type") not in (None, "object"):
            return [f"{path}: object where {node.get('type')} expected"]
        props = node.get("properties", {})
        errors = [f"{path}.{key}: not in the vendor schema" for key in value if key not in props]
        for key, item in value.items():
            if key in props:
                errors += _llama_violations(item, props[key], f"{path}.{key}")
        return errors
    if "enum" in node and value not in node["enum"]:
        return [f"{path}: {value!r} not in {node['enum']}"]
    if "const" in node and value != node["const"]:
        return [f"{path}: {value!r} != {node['const']!r}"]
    if node.get("type") == "null" and value is not None:
        return [f"{path}: expected null"]
    return []


def _llama_workflows():
    from app.models.types import llamaindex_workflow_fields

    fields = llamaindex_workflow_fields()
    base = {field.key: field.default for field in fields}
    yield base
    yield {**base, **{field.key: True for field in fields if field.kind == "bool"}, "tier": "agentic"}
    for field in fields:
        for value in field.values if field.kind == "enum" else ():
            yield {**base, field.key: value}
    yield {**base, "crop_enabled": True, "custom_prompt": "keep columns"}


def test_llamaindex_configuration_matches_the_vendor_schema() -> None:
    root = {"$ref": f"#/components/schemas/{_LLAMA_SCHEMA['root']}"}
    for workflow in _llama_workflows():
        payload, _ = protocol.llamaindex_configuration(workflow)
        payload["file_id"] = "file-1"
        assert _llama_violations(payload, root) == [], workflow


def test_llamaindex_exposes_every_vendor_tier_that_runs_a_model() -> None:
    from app.models.types import LLAMAINDEX_WORKFLOW_ENUMS

    vendor = _LLAMA_SCHEMA["schemas"]["ParseRequestConfiguration"]["properties"]["tier"]
    tiers = set(vendor["anyOf"][0]["enum"])
    # `fast` is rule-based (no model): Tabularium is a recognition tool, not a text extractor.
    assert LLAMAINDEX_WORKFLOW_ENUMS["tier"] == tiers - {"fast"}
