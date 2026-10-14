"""Server OpenAI-compatibile per Unlimited-OCR su Apple Silicon (MLX).

Perché esiste, invece di lanciare `mlx_vlm.server`: quel server, alla versione
0.7.1, **non inietta l'immagine** per l'architettura `unlimited_ocr`. Il
backend a continuous batching costruisce il prompt con i soli token di testo
(misurato: `prompt_tokens=6-7` contro i 1518 dell'API Python), quindi il modello
o restituisce vuoto o allucina da un prompt senza pagina. L'API Python
`mlx_vlm.generate` invece funziona (immagine espansa a 273 token + testo,
output grounded-markdown corretto), e questo server la espone con la stessa
interfaccia che Tabularium già parla (chat/completions, SSE).

Questo file **non** gira nel processo dashboard: lo lancia `serve_manager` con
l'interprete del venv MLX (`<root>/mlx-runtime`), esattamente come faceva con
`python -m mlx_vlm.server`. Gli import di `mlx_vlm`/FastAPI sono locali a
`main()` perché il processo che serve la UI non deve dipendere da MLX.

Uso (diretto, per debug):
    <root>/mlx-runtime/bin/python unlimited_ocr_mlx_server.py \
        --model mlx-community/Unlimited-OCR-bf16 --port 8080
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import threading
import time
import uuid
from typing import Any


def _decode_image(url: str) -> Any:
    """Da una URL immagine a qualcosa che `mlx_vlm.generate` accetta.

    Data-URI (base64 inline) → PIL Image (il preprocessore di mlx-vlm non
    accetta `BytesIO`: chiama `.copy()`); URL http(s) o path locale restano
    stringhe e li carica `load_image`.
    """
    if url.startswith("data:"):
        from PIL import Image

        _header, _sep, payload = url.partition(",")
        return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
    return url


def _extract(messages: list[dict]) -> tuple[str, list[Any]]:
    """Testo + immagini da una lista di messaggi OpenAI.

    L'ordine dei blocchi non conta: Tabularium può mandare text-image o
    image-text. Più immagini restano nell'ordine in cui compaiono.
    """
    texts: list[str] = []
    images: list[Any] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            kind = part.get("type")
            if kind == "text" and part.get("text"):
                texts.append(str(part["text"]))
            elif kind == "image_url":
                value = part.get("image_url")
                url = value.get("url") if isinstance(value, dict) else value
                if url:
                    images.append(_decode_image(url))
            elif kind == "input_image" and part.get("image_url"):
                images.append(_decode_image(part["image_url"]))
    return "\n".join(texts).strip(), images


def _normalize_text(text: str) -> str:
    """Toglie i segnaposto `<image>` che Tabularium manda per vLLM.

    `apply_chat_template` di mlx-vlm inserisce da sé il token immagine in base
    a `num_images`: lasciarlo anche nel testo aggiungerebbe un secondo
    segnaposto e disallineerebbe le feature visive.
    """
    return text.replace("<image>", " ").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Unlimited-OCR MLX OpenAI server")
    parser.add_argument("--model", required=True, help="repo/checkpoint MLX da servire")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--max-tokens", type=int, default=8192)
    # Su un Mac da 8 GB i pesi (3,7 GB in 8-bit) lasciano poco margine, e la
    # cache dei buffer di MLX trattiene memoria già liberata: misurato, la
    # memoria libera del sistema scende al 2% e il GPU va in timeout. Un tetto
    # alla cache (0 = nessuna cache) la restituisce subito al sistema.
    parser.add_argument("--cache-limit-gb", type=float, default=None)
    args = parser.parse_args()

    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, StreamingResponse

    from mlx_vlm import load
    from mlx_vlm.generate import stream_generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    model_id = args.model
    import mlx.core as mx

    if args.cache_limit_gb is not None:
        mx.set_cache_limit(int(args.cache_limit_gb * 1024 ** 3))
    model, processor = load(model_id)
    config = load_config(model_id)

    app = FastAPI(title="Tabularium Unlimited-OCR MLX")

    def generate(prompt: str, images: list, kwargs: dict):
        """`stream_generate`, ma un guasto del GPU chiude il processo.

        Dopo un timeout Metal ogni comando successivo viene rifiutato
        (`SubmissionsIgnored`) finché il processo vive: restare in piedi vuol
        dire far fallire tutte le pagine che seguono. Uscendo, Tabularium vede
        il server morto, lo riavvia e riprova la pagina.
        """
        try:
            yield from stream_generate(model, processor, prompt, images, **kwargs)
        except RuntimeError as exc:
            if "[METAL]" in str(exc):
                print(f"gpu_fault: {exc}; exiting so the server can be restarted", flush=True)
                threading.Timer(0.5, lambda: os._exit(75)).start()
            raise

    def release_memory() -> None:
        """Dopo ogni pagina: riporta il picco e restituisce la cache al sistema."""
        print(f"peak_gpu_gb={mx.get_peak_memory() / 1024 ** 3:.2f}", flush=True)
        mx.clear_cache()
        mx.reset_peak_memory()

    def build_prompt(text: str, num_images: int) -> str:
        return apply_chat_template(processor, config, _normalize_text(text), num_images=num_images)

    def gen_kwargs(body: dict) -> dict:
        kwargs: dict[str, Any] = {
            "max_tokens": int(body.get("max_tokens") or args.max_tokens),
            "temperature": float(body.get("temperature") if body.get("temperature") is not None else 0.0),
            # `skip_special_tokens=False`: i marker `<|det|>` fanno parte
            # dell'output che Tabularium deve poter leggere.
            "skip_special_tokens": False,
        }
        for key in ("top_p", "top_k", "repetition_penalty", "presence_penalty",
                    "frequency_penalty", "repetition_context_size"):
            if body.get(key) is not None:
                kwargs[key] = body[key]
        return kwargs

    @app.get("/v1/models")
    def list_models() -> dict:
        return {"object": "list", "data": [{"id": model_id, "object": "model", "created": 0}]}

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "model": model_id}

    def completion_id() -> str:
        return "chatcmpl-" + uuid.uuid4().hex

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict):
        text, images = _extract(body.get("messages") or [])
        if not images:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "nessuna immagine nella richiesta", "type": "invalid_request_error"}},
            )
        prompt = build_prompt(text, len(images))
        kwargs = gen_kwargs(body)
        created = int(time.time())
        cid = completion_id()
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        if not body.get("stream"):
            text_out = ""
            prompt_tokens = completion_tokens = 0
            finish = "stop"
            for result in generate(prompt, images, kwargs):
                text_out += result.text
                prompt_tokens = result.prompt_tokens
                completion_tokens = result.generation_tokens
                finish = getattr(result, "finish_reason", None) or finish
            release_memory()
            return {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": model_id,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text_out},
                    "finish_reason": finish,
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }

        def chunk(delta: dict, finish: str | None = None) -> dict:
            return {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        def event_stream():
            yield _sse(chunk({"role": "assistant", "content": ""}))
            prompt_tokens = completion_tokens = 0
            finish: str | None = None
            for result in generate(prompt, images, kwargs):
                prompt_tokens = result.prompt_tokens
                completion_tokens = result.generation_tokens
                finish = getattr(result, "finish_reason", None) or finish
                if result.text:
                    yield _sse(chunk({"content": result.text}))
            release_memory()
            yield _sse(chunk({}, finish or "stop"))
            if include_usage:
                yield _sse({
                    "id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model_id, "choices": [],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                })
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


if __name__ == "__main__":
    main()
