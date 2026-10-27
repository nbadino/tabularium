"""Protocol payloads and responses, as data.

Phase P4 of `docs/architecture/program.md`. A vendor protocol has two halves: the shape
of the request and the meaning of the answer (pure, and worth testing without a socket),
and the transport (timeouts, retries, auth headers). The first half lives here so that a
new protocol is a pair of functions and a test, not another branch inside a
fifteen-hundred-line client.
"""
from __future__ import annotations

import ast
import re
from typing import Any

# The Google `generationConfig` field for each sampling knob we expose. Names differ
# between the OpenAI surface and Google's: keeping the mapping here is what stops the
# two vocabularies from leaking into each other.
GOOGLE_SAMPLING = {"temperature": "temperature", "top_p": "topP", "top_k": "topK"}

# Default LlamaCloud host: the client reads it from here, so a spec or a test never
# has to repeat the string.
LLAMAINDEX_BASE_URL = "https://api.cloud.llamaindex.ai"


def google_request(
    prompt: str,
    image_data: str,
    *,
    max_tokens: int = 4096,
    sampling: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Body of one `generateContent` call: one image, one prompt, one generation."""
    generation: dict[str, Any] = {"maxOutputTokens": max(1, int(max_tokens))}
    for source, target in GOOGLE_SAMPLING.items():
        value = (sampling or {}).get(source)
        if value is not None:
            generation[target] = value
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": image_data}},
                    {"text": prompt},
                ],
            }
        ],
        "generationConfig": generation,
    }


def google_response(body: dict[str, Any]) -> tuple[str, str | None]:
    """The text of the first candidate and its finish reason.

    An empty `candidates` list is not an error: the provider answered, and the caller
    decides what an empty generation means (the layout path retries; the table path
    falls back to the crop).
    """
    candidates = body.get("candidates") or []
    if not candidates:
        return "", None
    candidate = candidates[0]
    finish_reason = candidate.get("finishReason")
    text = "".join(
        str(part.get("text") or "")
        for part in (candidate.get("content") or {}).get("parts") or []
    )
    return text, finish_reason


def google_usage(body: dict[str, Any]) -> dict[str, Any]:
    usage = body.get("usageMetadata") or {}
    return {
        "prompt_tokens": usage.get("promptTokenCount"),
        "completion_tokens": usage.get("candidatesTokenCount"),
    }


class OuterListScanner:
    """Rileva la chiusura della lista esterna ignorando parentesi nel testo."""

    def __init__(self) -> None:
        self.started = False
        self.depth = 0
        self.quote: str | None = None
        self.escaped = False
        self.complete = False

    def feed(self, chunk: str) -> bool:
        for char in chunk:
            if self.quote is not None:
                if self.escaped:
                    self.escaped = False
                elif char == "\\":
                    self.escaped = True
                elif char == self.quote:
                    self.quote = None
                continue
            if char in {"'", '"'}:
                self.quote = char
                continue
            if not self.started:
                if char == "[":
                    self.started = True
                    self.depth = 1
                continue
            if char in "[{(":
                self.depth += 1
            elif char in "]})":
                self.depth -= 1
                if self.depth == 0:
                    self.complete = char == "]"
                    return self.complete
        return False


def tolerant_items(text: str) -> list[dict]:
    """Parse tollerante dell'output layout (lista di dict con bbox/label)."""
    if text:
        try:
            val = ast.literal_eval(text.strip())
            if isinstance(val, list):
                return [d for d in val if isinstance(d, dict)]
        except (SyntaxError, ValueError, TypeError):
            pass
        items: list[dict] = []
        for block in re.findall(r"\{[^{}]*\}", text):
            try:
                d = ast.literal_eval(block)
                # "category" oltre a "label": alcuni adapter (es. dots.ocr, non
                # ancora verificato contro un server reale) usano quella chiave
                # per lo stesso campo.
                if isinstance(d, dict) and "bbox" in d and ("label" in d or "category" in d):
                    items.append(d)
            except (SyntaxError, ValueError, TypeError):
                continue
        return items
    return []


def llamaindex_base_url(value: str | None) -> str:
    """Accept a base URL or any LlamaCloud parse endpoint pasted by the user."""
    url = (value or LLAMAINDEX_BASE_URL).strip().rstrip("/")
    for suffix in (
        "/api/v2/parse",
        "/api/v2",
        "/api/v1/beta/files",
        "/api/v1/files",
        "/api/v1",
        "/v1",
    ):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url or LLAMAINDEX_BASE_URL


def llamaindex_pages(payload: dict) -> object:
    items = payload.get("items")
    if isinstance(items, dict) and isinstance(items.get("pages"), list):
        return items["pages"]
    job = payload.get("job")
    if isinstance(job, dict):
        job_items = job.get("items")
        if isinstance(job_items, dict) and isinstance(job_items.get("pages"), list):
            return job_items["pages"]
    if isinstance(payload.get("pages"), list):
        return payload["pages"]
    return []


def llamaindex_versions(payload: object) -> list[str]:
    found: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text and text not in found:
                found.append(text)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, dict):
            for key in ("version", "name", "id"):
                if isinstance(value.get(key), str):
                    collect(value[key])
                    return
            for child in value.values():
                if isinstance(child, (dict, list)):
                    collect(child)

    if isinstance(payload, dict):
        candidates = payload.get("versions", payload.get("data", payload))
        collect(candidates)
    return found


def llamaindex_configuration(workflow: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Translate validated settings into LlamaParse's parse request.

    Pure by construction: the caller fetches the workflow and checks that the adapter is
    the one this request belongs to. What is here is the mapping — including the two
    choices that protect the product: tables stay HTML (Markdown tables lose merged
    cells) and the timeouts are the job's, not the HTTP client's.
    """
    processing: dict = {
        "aggressive_table_extraction": bool(workflow.get("aggressive_table_extraction", True)),
        "disable_heuristics": bool(workflow.get("disable_heuristics", False)),
        "ocr_parameters": {"languages": list(workflow.get("languages") or ["eng"])},
        "ignore": {
            "diagonal_text": bool(workflow.get("ignore_diagonal_text", False)),
            "hidden_text": bool(workflow.get("ignore_hidden_text", False)),
            "text_in_image": bool(workflow.get("ignore_text_in_image", False)),
        },
    }
    if workflow.get("confidence_score_effort") == "high":
        processing["confidence_score_effort"] = "high"
    if workflow.get("specialized_chart_parsing") not in (None, "off"):
        processing["specialized_chart_parsing"] = workflow["specialized_chart_parsing"]
    if workflow.get("tier") == "agentic" and workflow.get("cost_optimizer"):
        processing["cost_optimizer"] = {"enable": True}

    markdown: dict = {
        "annotate_links": bool(workflow.get("markdown_annotate_links", True)),
        "annotate_revisions": bool(workflow.get("markdown_annotate_revisions", False)),
        "annotate_line_numbers": bool(workflow.get("markdown_annotate_line_numbers", False)),
        # Tabularium consumes table HTML to preserve merged cells. Asking
        # LlamaCloud for Markdown tables destroys that structure.
        "tables": {
            "output_tables_as_markdown": False,
            "compact_markdown_tables": bool(workflow.get("markdown_compact_tables", True)),
        },
    }
    payload: dict = {
        "tier": workflow.get("tier", "agentic"),
        "version": workflow.get("version", "latest"),
        "client_name": "tabularium",
        "disable_cache": bool(workflow.get("disable_cache", False)),
        "processing_options": processing,
        "output_options": {
            "watermark_handling": workflow.get("watermark_handling", "move_to_end"),
            "extract_printed_page_number": bool(workflow.get("extract_printed_page_number", False)),
            "markdown": markdown,
        },
        "processing_control": {
            "timeouts": {
                "base_in_seconds": int(workflow.get("job_timeout_in_seconds", 300)),
                "extra_time_per_page_in_seconds": int(
                    workflow.get("job_timeout_extra_time_per_page_in_seconds", 30)
                ),
            },
            "job_failure_conditions": {
                "allowed_failure_ratio": float(workflow.get("allowed_failure_ratio", 0.0)),
                "fail_on_job_timeout": bool(workflow.get("fail_on_job_timeout", False)),
                "fail_on_page_timeout": bool(workflow.get("fail_on_page_timeout", False)),
            },
        },
    }
    if workflow.get("camera_photo_correction"):
        payload["input_options"] = {"image": {"camera_photo_correction": True}}
    if workflow.get("crop_enabled"):
        crop = workflow.get("crop_box") or {}
        payload["crop_box"] = {
            edge: float(crop.get(edge, 0.0))
            for edge in ("top", "bottom", "left", "right")
        }
    custom_prompt = str(workflow.get("custom_prompt") or "").strip()
    if custom_prompt:
        payload["agentic_options"] = {"custom_prompt": custom_prompt}
    total_timeout = (
        int(workflow.get("job_timeout_in_seconds", 300))
        + int(workflow.get("job_timeout_extra_time_per_page_in_seconds", 30))
        + 60
    )
    return payload, total_timeout


def openai_content(
    data_uri: str,
    prompt: str,
    *,
    prompt_prefix: str = "",
    text_first: bool = False,
) -> list[dict[str, Any]]:
    """One user message with one image and one prompt, in the vendor's order.

    Most models read the image first; the ones that declare `text-image` expect the
    instruction before the pixels, and swapping it silently changes the answer.
    """
    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": data_uri}},
        {"type": "text", "text": f"{prompt_prefix}{prompt}"},
    ]
    if text_first:
        content.reverse()
    return content


def openai_request(
    model: str,
    content: list[dict[str, Any]],
    *,
    max_tokens: int,
    stream: bool,
    system_prompt: str | None = None,
    sampling: dict[str, Any] | None = None,
    adapter_id: str | None = None,
) -> dict[str, Any]:
    """Body of an OpenAI-compatible chat completion.

    The `no_repeat_ngram_size` knob is the one that does not travel in the sampling
    block: vLLM accepts it as an extra argument, and MinerU2.5 expects it inside
    `vllm_xargs` because its logits processor reads it from there. `sampling` is copied,
    never mutated: the caller may reuse or log it.
    """
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": stream,
        "messages": [
            *([{"role": "system", "content": system_prompt}] if system_prompt else []),
            {"role": "user", "content": content},
        ],
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    values = dict(sampling or {})
    no_repeat = values.pop("no_repeat_ngram_size", None)
    if no_repeat is not None:
        if adapter_id == "mineru2.5":
            values["vllm_xargs"] = {**(values.get("vllm_xargs") or {}), "no_repeat_ngram_size": no_repeat}
        else:
            payload["extra_args"] = {
                **(payload.get("extra_args") or {}),
                "no_repeat_ngram_size": no_repeat,
            }
    payload.update(values)
    return payload

