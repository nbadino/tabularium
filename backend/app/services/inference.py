"""Client inferenza via vLLM (endpoint OpenAI-compatibile).

Usato da valutazione (M6) e playground: layout completo, riconoscimento testo,
riconoscimento tabelle (OTSL). I bbox emessi dal modello sono in scala 0-1000.
"""
from __future__ import annotations

import ast
import base64
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable

import requests
from PIL import Image

from .. import config
from ..models import monkeyocrv2 as reference
from . import paddle_official
from ..adapters import inference_config as configstore
from ..domain import inference as protocol
from ..domain.inference import (
    OuterListScanner as _OuterListScanner,
    llamaindex_base_url as _llamaindex_base_url,
    llamaindex_pages as _llamaindex_pages,
    llamaindex_versions as _llamaindex_versions,
    tolerant_items as _tolerant_items,
)
from ..domain import otsl, table_headers
from .model_adapters import ModelAdapter, MonkeyOCRv2ParsingAdapter, get_adapter

# Fallback usati solo se l'adapter attivo non implementa ancora il task
# (`prompt_for` alza `NotImplementedError` o torna `None`): tengono il
# comportamento storico per MonkeyOCRv2 anche quando `VllmClient` viene
# costruito senza un adapter esplicito (retrocompatibilità dei chiamanti
# esistenti, es. test che istanziano `VllmClient(url=..., api_key=...)`).
LAYOUT_PROMPT = (
    "Please output the categories and coordinates of the document elements in reading order."
)
TEXT_PROMPT = "Please output the text content from the image."
TABLE_PROMPT = "Please extract the table from the image and represent it in OTSL format."
END2END_PROMPT = (
    "List the document elements in reading order, including their categories, "
    "coordinates, and the content of each element."
)

# Valori ammessi da `compute_profiles.provider` (CHECK in `db.py`): un provider
# esterno fuori elenco — il gateway OpenCode dei modelli chiusi — si registra
# come "custom".
_PROFILE_PROVIDERS = frozenset({"local", "ssh", "vast", "runpod", "modal", "custom", "llamaindex"})

from ..domain.inference import LLAMAINDEX_BASE_URL  # noqa: F401  (re-export)




class OutputTruncated(RuntimeError):
    """La generazione ha esaurito i token prima di chiudere l'output."""


def _prompt_for(adapter: ModelAdapter, task: str, label: str | None, fallback: str) -> str:
    """`adapter.prompt_for` con ripiego sulla costante storica SOLO quando è
    lo stesso adapter a scegliere di non prompare per quel task+label (torna
    `None`: v. `MonkeyOCRv2ParsingAdapter.prompt_for` per `label` "Column"/
    "Picture" — un default deliberato, non un buco).

    `NotImplementedError` (adapter che quel task non lo sa fare affatto, es.
    `PaddleOcrVlAdapter` per "layout") **non** ricade più sul prompt di
    MonkeyOCRv2: prima lo faceva, e mandava il prompt sbagliato a un modello
    che non lo capisce — degradazione silenziosa, non un errore. `layout()`/
    `recognize()`/`table_grid()` non la intercettano: risale a
    `model_prelabel_pages` (prefill.py) e ai due endpoint in blocks.py/
    evaluate.py, che già catturano `RuntimeError` (`NotImplementedError` ne è
    sottoclasse) attorno a queste chiamate e lo mostrano come errore di
    pagina/richiesta, non un crash.
    """
    return adapter.prompt_for(task, label) or fallback


def _with_table_hint(adapter: ModelAdapter, prompt: str, hint: str | None) -> str:
    """Accoda il hint di schema tabella al prompt, solo dove l'adapter lo accetta.

    Il hint è testo libero scritto dal progetto (es. «Year e Tons sono unite su
    una riga sopra»). Si aggiunge **dopo** il prompt ufficiale, separato da una
    riga vuota, così il modello vede prima l'istruzione canonica del checkpoint.
    Gli adapter con un token di protocollo o una pipeline nativa dichiarano
    `accepts_prompt_hint=False` e restano invariati: lì il testo sarebbe fuori
    distribuzione o romperebbe la modalità (v. `ModelCapabilities`).
    """
    text = (hint or "").strip()
    if not text or not getattr(adapter.capabilities, "accepts_prompt_hint", False):
        return prompt
    return f"{prompt}\n\n{text}"

# Semantica identica a `load_image` di `parsing/core_runner.py` (repo ufficiale):
#   - `min_pixels` INGRANDISCE le immagini piccole, non le riduce. Il pipeline
#     ufficiale passa 1003520 alla chiamata di layout, come *minimo*.
#   - il tetto superiore è la env `MOCR2_MAX_PIXELS`, che `parse.py` imposta
#     sempre dal suo `--max-pixels` (default 1003520) e che vale per TUTTE le
#     chiamate. Qui è `config.VLLM_MAX_PIXELS` / `TABULARIUM_VLLM_MAX_PIXELS`,
#     con lo stesso default; il campo "max pixels" in Impostazioni lo sovrascrive.
# Con entrambi a 1003520 la pagina arriva al layout esattamente a 1 MP, che è il
# punto di lavoro del checkpoint ufficiale.
# Attenzione a non confondere questo con `max_pixels 1003520` degli iperparametri
# di *training* (AGENTS.md §2.6): sono due cose diverse anche se il valore coincide.
LAYOUT_MIN_PIXELS = reference.PARSE_MAX_PIXELS

# Rete di sicurezza per il solo layout, usata quando il tetto globale è
# disattivato esplicitamente (`TABULARIUM_VLLM_MAX_PIXELS=0`): una scansione
# d'archivio Historic Shipping Index è 11.3 MP e a quella taglia il layout
# collassa. Misurato su pagina `A` del campione, a parità di tutto il resto:
#
#     11.3 MP → 2 blocchi sovrapposti e inutili
#      6.0 MP → 16 blocchi, frammentato
#      4.0 MP → 137 blocchi, esploso a livello di cella
#      2.0 MP → 5 blocchi: Title, numero, data, corpo
#      1.0 MP → 4 blocchi, corretto ma meno granulare   ← default ufficiale
#
# Il default resta quello del repo (1 MP, v. `config.VLLM_MAX_PIXELS`): la
# granularità in più a 2 MP era una nostra deviazione misurata, non il
# protocollo del checkpoint. Chi la rivuole imposta 2000000 come max pixels.
LAYOUT_MAX_PIXELS = 2_000_000

# Righe per banda nel riconoscimento tabella. Misurato su LSI_17186_015, tabella
# di 78 righe geometriche: con bande da 15 righe il modello ne restituisce 76 su
# 8 colonne stabili, con bande da 25 ne restituisce 59. Bande più corte costano
# più chiamate ma non perdono righe.
DEFAULT_ROWS_PER_BAND = 15

# Budget di uscita per il riconoscimento di una tabella, uguale per il crop
# intero (`recognize`) e per le bande (`table_grid`).
TABLE_MAX_TOKENS = 8192

# Il checkpoint supporta 40.960 token, ma su una RTX 4060 la cache KV disponibile
# consente in pratica un limite operativo di 24.576 (configurato in
# `scripts/serve_model.sh`). Il precedente 32.768 poteva comunque superare il
# contesto effettivo immagine + prompt + output e lasciare richieste orfane.
# 20.480 è un tetto di output, non il contesto totale: il parser rifiuta sempre
# una lista troncata. Sulla pagina indice più lunga osservata 16.384 non bastano.
END2END_MAX_TOKENS = 20_480


def _should_retry_repeat_output(raw: str) -> bool:
    """Rileva il loop di token tipico dei JSON di layout.

    Il caso più frequente è un coordinato che diventa ``998, 998, ...``;
    teniamo anche un controllo generico su n-grammi brevi per non legare la
    protezione a quel solo valore di bordo.
    """
    text = raw or ""
    if re.search(r"(?:\b998\b\s*,\s*){4,}\b998\b", text):
        return True
    tokens = re.findall(r"[A-Za-z0-9_]+|[^\w\s]", text)
    for size in range(1, min(24, len(tokens) // 5) + 1):
        repeats = 1
        cursor = len(tokens) - size
        while cursor >= size and tokens[cursor:cursor + size] == tokens[cursor - size:cursor]:
            repeats += 1
            cursor -= size
        if repeats >= 5:
            return True
    return False




# Sentinella per `_chat(max_pixels=…)`: "non passato" → usa il tetto del
# client. Non serve `None`, che significa genuinamente "nessun cap".
_CLIENT_MAX_PIXELS = object()


def _fit_pixels(
    image: Image.Image,
    *,
    min_pixels: int | None = None,
    max_pixels: int | None = None,
) -> Image.Image:
    """Porta l'immagine nell'intervallo di pixel richiesto, a proporzioni costanti.

    Port fedele della parte di ridimensionamento di `load_image` ufficiale. Le
    proporzioni restano invariate, quindi i bbox 0–1000 che il modello emette
    continuano a valere sull'immagine originale.
    """
    pixels = image.width * image.height
    if min_pixels and pixels < min_pixels:
        scale = (min_pixels / pixels) ** 0.5
    elif max_pixels and pixels > max_pixels:
        scale = (max_pixels / pixels) ** 0.5
    else:
        return image
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(size, Image.LANCZOS)




# Sentinella per distinguere "tetto non specificato dal chiamante" (usa il
# default di config) da "tetto rimosso di proposito" (None).
_MAX_PIXELS_DEFAULT = object()


class VllmClient:
    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: int = 180,
        max_retries: int = 2,
        adapter: ModelAdapter | None = None,
        max_pixels: int | None | object = _MAX_PIXELS_DEFAULT,
        provider: str | None = None,
        native_url: str | None = None,
    ) -> None:
        self.url = (url or config.VLLM_URL).rstrip("/")
        self.model = model or config.VLLM_MODEL
        self.api_key = api_key if api_key is not None else config.VLLM_API_KEY
        self.extra_headers = extra_headers if extra_headers is not None else config.VLLM_EXTRA_HEADERS
        self.timeout = timeout
        self.max_retries = max_retries
        # Provider dichiarato dalla configurazione persistita: fa da verità
        # anche quando l'URL mente (un tunnel SSH Vast ascolta su localhost,
        # ma la GPU è remota). None = si ripiega sulla sola forma dell'URL.
        self.provider = (provider or "").strip() or None
        self.native_url = (native_url or "").rstrip("/") or None
        # Default MonkeyOCRv2: i chiamanti esistenti (test compresi) costruiscono
        # `VllmClient` senza adapter e si aspettano il comportamento storico.
        self.adapter: ModelAdapter = adapter or MonkeyOCRv2ParsingAdapter()
        # Equivalente di `MOCR2_MAX_PIXELS`: vale per ogni chiamata al modello.
        # 0 o negativo = nessun tetto, come non impostare la env ufficiale.
        cap = max_pixels
        explicit_global_cap = False
        if max_pixels is _MAX_PIXELS_DEFAULT:
            # Do not apply MonkeyOCRv2's 1 MP preprocessing limit to unrelated
            # VLMs. Each recipe owns its recommendation; models without a
            # documented cap keep their original pixels for native processing.
            explicit_global_cap = "TABULARIUM_VLLM_MAX_PIXELS" in os.environ
            cap = config.VLLM_MAX_PIXELS if explicit_global_cap else None
        try:
            from . import model_settings

            settings = model_settings.get_settings(self.adapter.adapter_id)
            image_override = settings["overrides"].get("image", {})
            if "max_pixels" in image_override:
                cap = image_override["max_pixels"]
            elif max_pixels is _MAX_PIXELS_DEFAULT and not explicit_global_cap:
                recommended_cap = settings["recommended"]["image"].get("max_pixels")
                if recommended_cap is not None:
                    cap = recommended_cap
            if self.adapter.adapter_id == "qwen3-vl-8b":
                self.min_pixels = image_override.get("min_pixels")
            else:
                self.min_pixels = None
        except (ImportError, ValueError, KeyError, TypeError):
            self.min_pixels = None
        self.max_pixels: int | None = cap if (cap or 0) > 0 else None
        self.last_trace: dict = {}
        self.last_text = ""
        # Session id stabile per il gateway OpenCode: il suo routing e il
        # prompt caching lo usano per raggruppare le richieste della stessa
        # conversazione (header `x-opencode-session`, obbligatorio lì).
        self._session_id = uuid.uuid4().hex

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.extra_headers:
            headers.update(self.extra_headers)
        return headers

    def _llamaindex_headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.extra_headers:
            headers.update(self.extra_headers)
        return headers

    def _llamaindex_configuration(self) -> tuple[dict, int]:
        """Fetch this model's workflow and hand it to the protocol mapping."""
        if self.adapter.adapter_id != "llamaindex-ocria-1":
            raise RuntimeError("il client LlamaIndex richiede l'adapter OCRia-1")
        try:
            from . import model_settings

            workflow = model_settings.get_settings(self.adapter.adapter_id)["effective"]["workflow"]
        except (ImportError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("impostazioni OCRia-1 non disponibili") from exc
        return protocol.llamaindex_configuration(workflow)



    def llamaindex_native_page(
        self,
        image: Image.Image,
        *,
        cancel_event: threading.Event | None = None,
    ) -> list[dict]:
        """Upload one page, run a native LlamaParse job and poll its result."""
        if self.adapter.adapter_id != "llamaindex-ocria-1":
            raise RuntimeError("il parser nativo LlamaIndex richiede l'adapter OCRia-1")
        if not self.api_key:
            raise RuntimeError("API key LlamaIndex mancante: inseriscila in Modelli → OCRia-1")
        base = _llamaindex_base_url(self.url)
        configuration, total_timeout = self._llamaindex_configuration()
        headers = self._llamaindex_headers()
        started = time.perf_counter()
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")

        try:
            upload = requests.post(
                f"{base}/api/v1/beta/files",
                headers=headers,
                data={"purpose": "parse"},
                files={"file": ("page.png", buffer.getvalue(), "image/png")},
                timeout=(10, max(30, min(self.timeout, 300))),
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"LlamaCloud non raggiungibile durante l'upload: {exc}") from exc
        if upload.status_code in (401, 403):
            raise RuntimeError("API key LlamaIndex rifiutata durante l'upload (HTTP 401/403)")
        if upload.status_code >= 400:
            raise RuntimeError(f"upload LlamaCloud fallito (HTTP {upload.status_code}): {upload.text[:300]}")
        try:
            uploaded = upload.json()
        except ValueError as exc:
            raise RuntimeError("LlamaCloud ha restituito un upload non JSON") from exc
        file_node = uploaded.get("file") if isinstance(uploaded, dict) else None
        file_id = (
            uploaded.get("id") or uploaded.get("file_id")
            if isinstance(uploaded, dict) else None
        ) or (file_node.get("id") if isinstance(file_node, dict) else None)
        if not file_id:
            raise RuntimeError("LlamaCloud non ha restituito l'id del file caricato")

        try:
            start = requests.post(
                f"{base}/api/v2/parse",
                headers=self._headers(),
                json={"file_id": file_id, **configuration},
                timeout=(10, max(30, min(self.timeout, 300))),
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"LlamaCloud non raggiungibile durante l'avvio del job: {exc}") from exc
        if start.status_code in (401, 403):
            raise RuntimeError("API key LlamaIndex rifiutata all'avvio del job (HTTP 401/403)")
        if start.status_code >= 400:
            raise RuntimeError(f"avvio parse LlamaCloud fallito (HTTP {start.status_code}): {start.text[:500]}")
        try:
            started_job = start.json()
        except ValueError as exc:
            raise RuntimeError("LlamaCloud ha restituito un avvio job non JSON") from exc
        job_node = started_job.get("job") if isinstance(started_job, dict) else None
        job_id = (
            started_job.get("id") or started_job.get("job_id")
            if isinstance(started_job, dict) else None
        ) or (job_node.get("id") if isinstance(job_node, dict) else None)
        if not job_id:
            raise RuntimeError("LlamaCloud non ha restituito l'id del job di parsing")

        deadline = time.monotonic() + total_timeout
        delay = 1.5
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("parsing LlamaIndex annullato")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"job LlamaIndex {job_id} oltre il limite di {total_timeout}s")
            try:
                response = requests.get(
                    f"{base}/api/v2/parse/{job_id}",
                    headers=headers,
                    params={"expand": "items,metadata,usage,job_metadata"},
                    timeout=(10, max(15, min(self.timeout, 120))),
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"polling LlamaCloud interrotto: {exc}") from exc
            if response.status_code in (401, 403):
                raise RuntimeError("API key LlamaIndex rifiutata durante il polling (HTTP 401/403)")
            if response.status_code >= 400:
                raise RuntimeError(f"polling LlamaCloud fallito (HTTP {response.status_code}): {response.text[:300]}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("LlamaCloud ha restituito un polling non JSON") from exc
            current = payload.get("job") if isinstance(payload, dict) and isinstance(payload.get("job"), dict) else payload
            status = str(current.get("status") or current.get("state") or "").lower()
            if status in {"completed", "success", "succeeded", "done"}:
                pages = _llamaindex_pages(payload)
                items = self.adapter.parse_native_result(
                    pages, image_width=image.width, image_height=image.height
                )
                elapsed = time.perf_counter() - started
                usage = payload.get("usage") or current.get("usage") or {}
                self.last_trace = {
                    "elapsed_s": round(elapsed, 3),
                    "job_id": str(job_id),
                    "status": status,
                    "pages": len(pages) if isinstance(pages, list) else 0,
                    "items": len(items),
                    "usage": usage,
                    "tier": configuration.get("tier"),
                    "version": configuration.get("version"),
                }
                return items
            if status in {"failed", "failure", "error", "cancelled", "canceled"}:
                reason = (
                    current.get("error_message")
                    or current.get("error")
                    or current.get("failure_reason")
                    or status
                )
                raise RuntimeError(f"job LlamaIndex {job_id} terminato con stato {status}: {reason}")
            time.sleep(min(delay, max(0.2, deadline - time.monotonic())))
            delay = min(5.0, delay * 1.35)

    def teleocr_native_page(
        self,
        image: Image.Image,
        *,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict]:
        """Run the official TeleOCR page pipeline on its cloud-side gateway."""
        if self.adapter.adapter_id != "teleocr":
            raise RuntimeError("il runner nativo TeleOCR richiede l'adapter TeleOCR")
        if not self.native_url:
            raise RuntimeError(
                "runner TeleOCR ufficiale non raggiungibile: collega il tunnel Vast "
                "dell'istanza preparata con la ricetta TeleOCR"
            )
        # TeleOCR is the only pipeline that also takes the layout mode as a header:
        # Detection or Segmentation, a choice the user makes in Settings.
        layout_mode = "Detection"
        try:
            from . import model_settings

            layout_mode = str(
                model_settings.get_settings("teleocr")["effective"]
                .get("workflow", {})
                .get("layout_mode")
                or "Detection"
            )
        except (ImportError, KeyError, ValueError):
            pass
        headers = self._native_vendor_headers(
            "teleocr",
            "teleocr",
            include_max_pixels=True,
            extra_headers={"x-teleocr-layout-mode": layout_mode},
        )
        payload = self._native_parse_page(image, headers=headers, vendor="TeleOCR")
        return self.adapter.parse_native_result(payload.get("blocks"))

    def _native_vendor_headers(
        self,
        adapter_id: str,
        prefix: str,
        *,
        include_max_pixels: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Headers the vendor pipelines read: auth, sampling, optional pixel cap.

        The pipelines are sibling processes (GLM-OCR, MinerU) that take the vendor's
        knobs as headers rather than in the body. One helper builds them, so a new
        native model does not copy the same five lines and forget one.
        """
        headers = self._headers()
        headers.pop("Content-Type", None)
        try:
            from . import model_settings

            effective = model_settings.get_settings(adapter_id)["effective"]
            generation = effective.get("generation", {})
            if generation:
                headers[f"x-{prefix}-generation"] = json.dumps(generation, separators=(",", ":"))
            if include_max_pixels:
                max_pixels = effective.get("image", {}).get("max_pixels")
                if max_pixels is not None:
                    headers[f"x-{prefix}-max-pixels"] = str(max_pixels)
        except (ImportError, KeyError, ValueError):
            pass
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _native_parse_page(
        self, image: Image.Image, *, headers: dict[str, str], vendor: str
    ) -> dict:
        """POST one page to a vendor pipeline and return its JSON body."""
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        try:
            response = requests.post(
                f"{self.native_url}/parse",
                data=buffer.getvalue(),
                headers=headers,
                timeout=max(self.timeout, 600),
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"pipeline ufficiale {vendor} non disponibile: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"risposta non JSON dal pipeline ufficiale {vendor}") from exc








    def qwen_native_page(
        self,
        image: Image.Image,
        *,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict]:
        """Run QwenVL's documented full-page ``qwenvl html`` parser."""
        if self.adapter.adapter_id != "qwen3-vl-8b":
            raise RuntimeError("il parser documenti QwenVL richiede l'adapter Qwen3-VL")
        raw = self._chat(
            image,
            "qwenvl html",
            task="layout",
            max_tokens=self._output_budget(16384),
            sampling=self._sampling_for("layout"),
            on_delta=on_delta,
            cancel_event=cancel_event,
        )
        items = self.adapter.parse_native_result(raw)
        if not items:
            raise RuntimeError(
                "QwenVL HTML non contiene blocchi con data-bbox valido; "
                "il modello ha restituito contenuto senza coordinate di layout"
            )
        return items

    def glmocr_native_page(
        self,
        image: Image.Image,
        *,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict]:
        """Run the vendor's self-hosted GLM-OCR layout+region OCR pipeline."""
        if self.adapter.adapter_id != "glm-ocr":
            raise RuntimeError("il runner nativo GLM-OCR richiede l'adapter GLM-OCR")
        if not self.native_url:
            raise RuntimeError(
                "pipeline ufficiale GLM-OCR non raggiungibile: collega il tunnel Vast "
                "dell'istanza preparata con la ricetta GLM-OCR"
            )
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        headers = self._headers()
        headers.pop("Content-Type", None)
        try:
            from . import model_settings

            effective = model_settings.get_settings("glm-ocr")["effective"]
            generation = effective.get("generation", {})
            if generation:
                headers["x-glmocr-generation"] = json.dumps(generation, separators=(",", ":"))
            max_pixels = effective.get("image", {}).get("max_pixels")
            if max_pixels is not None:
                headers["x-glmocr-max-pixels"] = str(max_pixels)
        except (ImportError, KeyError, ValueError):
            pass
        try:
            response = requests.post(
                f"{self.native_url}/parse",
                data=buffer.getvalue(),
                headers=headers,
                timeout=max(self.timeout, 600),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"pipeline ufficiale GLM-OCR non disponibile: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError("risposta non JSON dal pipeline ufficiale GLM-OCR") from exc
        return self.adapter.parse_native_result(payload.get("json_result"))

    def mineru_native_page(
        self,
        image: Image.Image,
        *,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict]:
        """Run OpenDataLab's MinerUClient pipeline through the cloud gateway."""
        if self.adapter.adapter_id != "mineru2.5":
            raise RuntimeError("il runner nativo MinerU richiede l'adapter MinerU2.5")
        if not self.native_url:
            raise RuntimeError(
                "pipeline ufficiale MinerU non raggiungibile: avvia il runner nativo "
                "della destinazione selezionata (Vast.ai o MLX locale)"
            )
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        headers = self._headers()
        headers.pop("Content-Type", None)
        try:
            from . import model_settings

            generation = model_settings.get_settings("mineru2.5")["effective"].get("generation", {})
            if generation:
                headers["x-mineru-generation"] = json.dumps(generation, separators=(",", ":"))
        except (ImportError, KeyError, ValueError):
            pass
        try:
            response = requests.post(
                f"{self.native_url}/parse",
                data=buffer.getvalue(),
                headers=headers,
                timeout=max(self.timeout, 600),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"pipeline ufficiale MinerU non disponibile: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError("risposta non JSON dal pipeline ufficiale MinerU") from exc
        return self.adapter.parse_native_result(payload.get("blocks"))

    def paddle_native_page(
        self,
        image: Image.Image,
        *,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict]:
        """PaddleOCR-VL's official pipeline: layout plus recognition in one call.

        The serve backend is a property of the deployment, not of the page: MLX on a
        local Mac, vLLM everywhere else. The vendor returns pixel boxes; they leave this
        method in the 0-1000 convention the rest of the pipeline speaks.
        """
        if self.adapter.adapter_id != "paddleocr-vl":
            raise RuntimeError("il runner nativo PaddleOCR-VL richiede l'adapter PaddleOCR-VL")
        backend = "vllm-server"
        if self.provider == "local":
            from . import hardware

            if hardware.pick_serve_runtime(self.adapter.capabilities) == hardware.RUNTIME_MLX:
                backend = "mlx-vlm-server"
        items = paddle_official.parse_page(
            image, self.url, self.model, image.width, image.height,
            vl_rec_backend=backend,
        )
        for item in items:
            bbox = item.get("bbox") or []
            if len(bbox) == 4:
                item["bbox"] = [
                    round(float(bbox[0]) / image.width * 1000),
                    round(float(bbox[1]) / image.height * 1000),
                    round(float(bbox[2]) / image.width * 1000),
                    round(float(bbox[3]) / image.height * 1000),
                ]
        return items

    @property
    def is_cloud(self) -> bool:
        """Indica se l'endpoint punta a un server remoto/cloud o alla macchina locale.

        Il provider persistito vince sull'URL: un tunnel SSH Vast o un proxy
        con port-forward ascoltano su ``localhost``, quindi la sola forma
        dell'URL direbbe «locale» per una GPU che non lo è.
        """
        if self.provider == "local":
            return False
        if self.provider:
            return True
        url_lower = self.url.lower()
        return not any(
            loc in url_lower
            for loc in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "local")
        )

    @property
    def is_modal(self) -> bool:
        """Modal usa un proxy HTTP davanti al web server vLLM.

        Il proxy può troncare una risposta chunked molto lunga con
        ``TransferEncodingError`` anche quando vLLM ha terminato normalmente;
        il client lo evita drenando lo stream fino a ``[DONE]``.
        """
        return ".modal.run" in self.url.lower()

    def _chat(
        self,
        image: Image.Image,
        prompt: str,
        max_tokens: int = 4096,
        min_pixels: int | None = None,
        max_pixels: int | None = _CLIENT_MAX_PIXELS,
        total_timeout: float | None = None,
        stop_when_complete_list: bool = False,
        sampling: dict | None = None,
        on_delta: Callable[[str], None] | None = None,
        *,
        task: str = "text",
        cancel_event: threading.Event | None = None,
    ) -> str:
        # Alcuni modelli chiusi (Gemini 4 Argon) non parlano OpenAI: il loro
        # endpoint è il `generateContent` di Google. Il resto della pipeline
        # non cambia — stesso prompt dall'adapter, stessa risposta di testo.
        if getattr(self.adapter, "chat_protocol", "") == "google-generatecontent":
            return self._generate_content(
                image,
                prompt,
                max_tokens=max_tokens,
                total_timeout=total_timeout,
                sampling=sampling,
                on_delta=on_delta,
                cancel_event=cancel_event,
            )
        buf = io.BytesIO()
        # Cap effettivo: default storico = tetto del client (MOCR2-style);
        # `None` esplicito = nessun ridimensionamento client-side (il cap vero
        # lo applica il preprocessore di vLLM dal config del checkpoint); un
        # adapter che dichiara `native_image_resolution` esonera il proprio
        # protocollo immagine da qualsiasi cap client (v. MinerU2.5).
        if max_pixels is _CLIENT_MAX_PIXELS:
            cap = (
                None
                if getattr(self.adapter, "native_image_resolution", False)
                else self.max_pixels
            )
        else:
            cap = max_pixels
        if min_pixels is None and self.adapter.adapter_id == "qwen3-vl-8b":
            min_pixels = self.min_pixels
        prepared = _fit_pixels(image, min_pixels=min_pixels, max_pixels=cap)
        prepared.convert("RGB").save(buf, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        # Alcuni client upstream usano risposte OpenAI non-streaming. Manteniamo
        # il loro protocollo; gli adapter senza preferenza dichiarata conservano
        # lo streaming usato dalla UI.
        stream_response = bool(getattr(self.adapter, "stream_response", True))
        prompt_prefix = str(getattr(self.adapter, "image_prompt_prefix", "") or "")
        content = protocol.openai_content(
            data_uri,
            prompt,
            prompt_prefix=prompt_prefix,
            text_first=getattr(self.adapter, "multimodal_content_order", "image-text") == "text-image",
        )
        payload = protocol.openai_request(
            self.model,
            content,
            max_tokens=max_tokens,
            stream=stream_response,
            system_prompt=getattr(self.adapter, "system_prompt", None),
            sampling=sampling,
            adapter_id=self.adapter.adapter_id,
        )
        request_overrides = getattr(self.adapter, "request_overrides", None)
        if callable(request_overrides):
            payload.update(request_overrides(task) or {})
        effective_max_tokens = payload.get("max_tokens")
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            first_token_at: float | None = None
            chunks: list[str] = []
            usage: dict = {}
            finish_reason: str | None = None
            scanner = _OuterListScanner()
            logical_done = False
            try:
                with requests.post(
                    f"{self.url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                    timeout=(10, self.timeout),
                    stream=stream_response,
                    allow_redirects=False,
                ) as resp:
                    resp.raise_for_status()
                    if not stream_response:
                        event = resp.json()
                        usage = event.get("usage") or {}
                        choices = event.get("choices") or []
                        if choices:
                            choice = choices[0]
                            finish_reason = choice.get("finish_reason") or finish_reason
                            message = choice.get("message") or {}
                            delta = message.get("content") or ""
                            if delta:
                                chunks.append(delta)
                                if on_delta:
                                    on_delta(delta)
                                if stop_when_complete_list:
                                    scanner.feed(delta)
                    else:
                        for line in resp.iter_lines(decode_unicode=True):
                            if cancel_event is not None and cancel_event.is_set():
                                raise RuntimeError("inference cancelled")
                            elapsed = time.perf_counter() - started
                            if total_timeout is not None and elapsed > total_timeout:
                                raise TimeoutError(
                                    f"generazione oltre il limite di {total_timeout:.0f}s"
                                )
                            if not line or not line.startswith("data: "):
                                continue
                            body = line[6:]
                            if body == "[DONE]":
                                break
                            event = json.loads(body)
                            if event.get("usage"):
                                usage = event["usage"]
                            choices = event.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0]
                            finish_reason = choice.get("finish_reason") or finish_reason
                            delta = (choice.get("delta") or {}).get("content") or ""
                            if not delta:
                                continue
                            if first_token_at is None:
                                first_token_at = time.perf_counter()
                            if logical_done:
                                # Il parser ha già trovato la lista completa,
                                # ma dobbiamo comunque drenare fino a [DONE].
                                continue
                            if on_delta:
                                on_delta(delta)
                            chunks.append(delta)
                            if stop_when_complete_list and scanner.feed(delta):
                                finish_reason = "complete_list"
                                logical_done = True
                ended = time.perf_counter()
                text = "".join(chunks)
                self.last_text = text
                completion_tokens = usage.get("completion_tokens")
                self.last_trace = {
                    "elapsed_s": round(ended - started, 3),
                    "ttft_s": (
                        round(first_token_at - started, 3)
                        if first_token_at is not None
                        else None
                    ),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": completion_tokens,
                    "tokens_per_s": (
                        round(completion_tokens / (ended - first_token_at), 2)
                        if completion_tokens and first_token_at and ended > first_token_at
                        else None
                    ),
                    "finish_reason": finish_reason,
                    "max_tokens": effective_max_tokens,
                    "chars": len(text),
                    "complete_list": scanner.complete if stop_when_complete_list else None,
                }
                if stop_when_complete_list and not scanner.complete:
                    raise OutputTruncated(
                        "output END2END incompleto "
                        f"(finish={finish_reason}, chars={len(text)}, max_tokens={effective_max_tokens})"
                    )
                return text
            except Exception as exc:  # noqa: BLE001
                last = exc
                # Ritentare una generazione già iniziata moltiplica il costo e
                # può lasciare richieste orfane. I retry servono solo a errori
                # di connessione avvenuti prima del primo token.
                if chunks or isinstance(exc, TimeoutError):
                    break
        # «Non raggiungibile» solo quando lo è davvero: un'uscita troncata o
        # un 400 arrivano da un server che ha risposto, e chiamarli guasti di
        # rete mandava a cercare il problema nel tunnel.
        if isinstance(last, OutputTruncated):
            raise last
        if isinstance(last, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            raise RuntimeError(f"vLLM non raggiungibile ({self.url}): {last}")
        raise RuntimeError(f"vLLM ({self.url}): {last}")

    def _google_base_url(self) -> str:
        """Base del protocollo Google, tollerante alla forma dell'URL salvato.

        L'utente può incollare l'endpoint del modello per intero (come nella
        documentazione OpenCode: `.../models/gemini-4-argon-contributor`) o la
        sola base `.../v1beta`. Entrambi devono produrre la stessa richiesta:
        tutto ciò che è `/models/<id>` o `:generateContent` viene tolto e il
        modello lo si prende da `self.model`.
        """
        url = self.url.rstrip("/")
        if url.endswith(":generateContent"):
            url = url[: -len(":generateContent")]
        marker = "/models/"
        if marker in url:
            url = url.split(marker, 1)[0]
        return url

    def _generate_content(
        self,
        image: Image.Image,
        prompt: str,
        *,
        max_tokens: int = 4096,
        total_timeout: float | None = None,
        sampling: dict | None = None,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Una generazione dal protocollo Google `generateContent`.

        Non streaming (una richiesta, una risposta): il client OpenCode/Google
        qui non offre un percorso di streaming usato dal resto del codice, e
        una risposta unica è più semplice da tracciare. `sampling` copre solo i
        campi che `generationConfig` accetta davvero.
        """
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("inference cancelled")
        prepared = _fit_pixels(image, max_pixels=self.max_pixels).convert("RGB")
        buf = io.BytesIO()
        prepared.save(buf, format="PNG")
        data = base64.b64encode(buf.getvalue()).decode("ascii")

        generation: dict = {"maxOutputTokens": max(1, int(max_tokens))}
        for key in ("temperature", "topP", "topK"):
            source = {"temperature": "temperature", "topP": "top_p", "topK": "top_k"}[key]
            if sampling and sampling.get(source) is not None:
                generation[key] = sampling[source]
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": "image/png", "data": data}},
                        {"text": prompt},
                    ],
                }
            ],
            "generationConfig": generation,
        }
        headers = self._headers()
        # L'header è obbligatorio sul gateway OpenCode e innocuo altrove;
        # l'User-Agent generico viene rifiutato dal bordo Cloudflare.
        headers["x-opencode-session"] = self._session_id
        headers.setdefault("User-Agent", "tabularium/1.0")
        # Google nativo usa `x-goog-api-key`; il gateway OpenCode accetta
        # anche `Authorization: Bearer`, già presente in `_headers`. Mando
        # entrambi per non dipendere dalla via.
        if self.api_key:
            headers.setdefault("x-goog-api-key", self.api_key)

        endpoint = f"{self._google_base_url()}/models/{self.model}:generateContent"
        started = time.perf_counter()
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=(10, total_timeout or self.timeout),
            )
            response.raise_for_status()
            body = response.json()
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"gateway del modello non raggiungibile ({self.url}): {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"risposta non JSON dal gateway ({self.url})") from exc
        ended = time.perf_counter()

        text, finish_reason = protocol.google_response(body)
        self.last_text = text
        self.last_trace = {
            "elapsed_s": round(ended - started, 3),
            **protocol.google_usage(body),
            "finish_reason": finish_reason,
            "max_tokens": payload["generationConfig"]["maxOutputTokens"],
            "chars": len(text),
        }
        if on_delta and text:
            on_delta(text)
        return text

    def _parse_items(self, raw: str) -> list[dict]:
        """Delega il parsing all'adapter se ne dichiara uno proprio (es.
        `MinerU2_5Adapter`, formato a token speciali, non il JSON tollerante
        di MonkeyOCRv2); altrimenti usa `_tolerant_items` come sempre."""
        parse = getattr(self.adapter, "parse_layout", None)
        return parse(raw) if callable(parse) else _tolerant_items(raw)

    def _output_budget(self, wanted: int) -> int:
        """`max_tokens` che sta nel contesto con cui l'adapter serve il modello.

        vLLM **rifiuta** con 400 una richiesta il cui `max_tokens` supera il
        contesto, non la tronca: chiedere 8192 token di uscita a un adapter
        servito con `--max-model-len 8192` fa fallire la chiamata invece di
        produrre una tabella più corta. Misurato su DeepSeek-OCR-2. Il margine
        è per l'immagine e il prompt, che occupano lo stesso contesto.
        """
        context = getattr(self.adapter.capabilities, "max_model_len", 0) or 0
        if context <= 0:
            return wanted
        return max(512, min(wanted, context - 2048))

    def _uses_local_mlx(self) -> bool:
        """Vero quando questo endpoint è il server MLX locale di questa macchina.

        Non basta l'URL: un tunnel Vast ascolta su localhost. Si guarda prima
        il provider persistito (`is_cloud`), poi il runtime che *questa*
        macchina sceglierebbe per l'adapter.
        """
        if self.is_cloud:
            return False
        try:
            from . import hardware
            return hardware.pick_serve_runtime(self.adapter.capabilities) == hardware.RUNTIME_MLX
        except Exception:  # noqa: BLE001
            return False

    def _sampling_for(self, task: str) -> dict | None:
        """Override di sampling per task, se l'adapter ne dichiara (es.
        le penalità anti-ripetizione richieste da MinerU2.5)."""
        sampling_for = getattr(self.adapter, "sampling_for", None)
        sampling = dict(sampling_for(task) or {}) if callable(sampling_for) else {}
        try:
            from . import model_settings
            settings = model_settings.get_settings(self.adapter.adapter_id)
            sampling.update(settings["effective"]["generation"])
            # Sul percorso MLX la ricetta ufficiale non ha il logits processor
            # n-gram (mlx-vlm non lo fornisce, e il server non accetta processor
            # callable via JSON): l'unico guard è la penalità di ripetizione.
            # Vale solo per il serving locale Apple Silicon, mai per vLLM, dove
            # il modello ha già il suo processore n-gram.
            if self._uses_local_mlx():
                mlx = settings["effective"].get("mlx") or {}
                gen_overrides = (settings.get("overrides") or {}).get("generation") or {}
                # Il guard vince sul default della ricetta (Qwen porta
                # `repetition_penalty` a 1.0): senza questo l'override resterebbe
                # un no-op per via del `setdefault`. Un valore scelto dall'utente
                # in Impostazioni ha comunque la precedenza.
                if (
                    mlx.get("repetition_penalty") is not None
                    and "repetition_penalty" not in gen_overrides
                ):
                    sampling["repetition_penalty"] = float(mlx["repetition_penalty"])
                if mlx.get("repetition_context_size") is not None:
                    sampling["repetition_context_size"] = int(mlx["repetition_context_size"])
                # La presence penalty ufficiale di Qwen3-VL vale solo se la
                # finestra copre una riga di registro: a 20 token (default di
                # mlx-vlm) la ripetizione resta invisibile.
                if mlx.get("presence_context_size") is not None:
                    sampling["presence_context_size"] = int(mlx["presence_context_size"])
        except (ImportError, ValueError, KeyError):
            pass
        return sampling or None

    def layout(
        self, image: Image.Image, on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        total_timeout: float | None = None,
    ) -> list[dict]:
        # Layout: minimo e tetto ufficiali coincidono a 1 MP, quindi la pagina
        # arriva al VLM esattamente come in `get_layout()` del repo. Un override
        # esplicito (Impostazioni / env) sostituisce il tetto; se il tetto è
        # disattivato resta `LAYOUT_MAX_PIXELS` come rete di sicurezza.
        prompt = _prompt_for(self.adapter, "layout", None, LAYOUT_PROMPT)
        official_size = getattr(self.adapter, "official_layout_size", None)
        if official_size:
            # MinerUClient.prepare_for_layout() usa resize() a una dimensione
            # fissa di 1036x1036. È parte del protocollo del checkpoint, non
            # un'ottimizzazione nostra: aspect ratio e pixel budget diversi
            # cambiano il layout che il VLM vede. E il tetto 1 MP del client
            # NON tocca questa immagine: 1036x1036 = 1.073.296 pixel > cap,
            # una seconda passata di _fit_pixels la restringeva a ~1001x1001
            # e il modello allucinava una griglia di micro-blocchi
            # `page_number` (riprodotto live su MinerU2.5, 2026-09-02).
            prepared = image.convert("RGB").resize(official_size, Image.Resampling.BICUBIC)
            raw = self._chat(
                prepared,
                prompt,
                task="layout",
                max_tokens=4096,
                max_pixels=None,
                total_timeout=total_timeout,
                sampling=self._sampling_for("layout"),
                on_delta=on_delta,
                cancel_event=cancel_event,
            )
        else:
            prepared = _fit_pixels(
                image,
                min_pixels=LAYOUT_MIN_PIXELS,
                max_pixels=self.max_pixels or LAYOUT_MAX_PIXELS,
            )
            raw = self._chat(
                prepared,
                prompt,
                task="layout",
                max_tokens=4096,
                total_timeout=total_timeout,
                sampling=self._sampling_for("layout"),
                on_delta=on_delta,
                cancel_event=cancel_event,
            )
        cleaned = []
        for item in self._parse_items(raw):
            bbox = item.get("bbox")
            label = item.get("label") or item.get("category")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                values = [float(v) for v in bbox]
            except (TypeError, ValueError):
                continue
            cleaned.append({"bbox": values, "label": str(label)})
        return cleaned

    def end2end(
        self,
        image: Image.Image,
        max_tokens: int = END2END_MAX_TOKENS,
        max_pixels: int | None = None,
        total_timeout: float = 300,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        on_retry: Callable[[], None] | None = None,
    ) -> list[dict]:
        """Pagina intera: bbox, label e contenuto in una sola generazione.

        È il percorso ufficiale ``ALL_PROMPT['END2END']``. Vale lo stesso tetto
        `MOCR2_MAX_PIXELS` di tutte le altre chiamate (1 MP di default,
        v. `config.VLLM_MAX_PIXELS`); un `max_pixels` esplicito lo sostituisce.
        """
        prepared = _fit_pixels(
            image,
            # END2END segue il contratto ufficiale della pagina intera: stesso
            # tetto globale del resto della pipeline, nessuna regola separata.
            max_pixels=max_pixels or self.max_pixels,
        )
        prompt = _prompt_for(self.adapter, "end2end", None, END2END_PROMPT)
        end2end_tokens = max_tokens
        # Vendor-native grounded parsers may publish a task-specific output
        # ceiling in addition to the generic endpoint budget.
        if getattr(self.adapter, "end2end_output_format", "list") == "grounded-markdown":
            native_budget = getattr(self.adapter, "end2end_max_tokens", None)
            if native_budget is not None:
                end2end_tokens = min(end2end_tokens, int(native_budget))
        sampling = self._sampling_for("end2end")
        raw = self._chat(
            prepared,
            prompt,
            task="end2end",
            max_tokens=end2end_tokens,
            total_timeout=total_timeout,
            # Gli adapter JSON possono chiudere presto dopo la lista; questo
            # modello invece emette markdown con marker <|det|>.
            stop_when_complete_list=getattr(self.adapter, "end2end_output_format", "list") == "list",
            sampling=sampling,
            on_delta=on_delta,
            cancel_event=cancel_event,
        )

        def valid_items(value: str) -> list[dict]:
            parsed = self._parse_items(value)
            return [
                item for item in parsed
                if isinstance(item.get("bbox"), (list, tuple))
                and len(item["bbox"]) == 4
                and (item.get("label") or item.get("category"))
            ]

        # Il repo ufficiale riconosce il loop di token e ritenta con un
        # campionamento leggermente meno rigido. Applichiamo la stessa idea al
        # client HTTP, ma solo se il primo output è patologico: un END2END
        # normale resta una sola chiamata. Questo evita di salvare bbox come
        # [509, 21, 960, 998, 998, ...].
        first_valid = valid_items(raw)
        # Only MonkeyOCR's upstream runner defines this anti-repeat retry.
        # Other adapters own their own generation protocol and must not get a
        # Tabularium-invented second sampling pass.
        monkey_native_retry = getattr(self.adapter, "adapter_id", None) == "monkeyocrv2-parsing"
        # Grounded-markdown (Unlimited-OCR) is a native protocol, not a JSON
        # list. An empty parsed bbox list must not trigger the generic
        # MonkeyOCR retry: the raw response is still the authoritative output
        # to inspect and a second 8 GB generation can exceed the request
        # budget without adding evidence.
        native_grounded = getattr(self.adapter, "end2end_output_format", "list") == "grounded-markdown"
        if not native_grounded and (
            (not first_valid and monkey_native_retry)
            or (first_valid and monkey_native_retry and _should_retry_repeat_output(raw))
        ):
            if on_retry:
                on_retry()
            retry_sampling = dict(sampling or {})
            retry_sampling.update({"temperature": 0.2, "top_p": 0.95})
            retry_raw = self._chat(
                prepared,
                prompt,
                task="end2end",
                max_tokens=end2end_tokens,
                total_timeout=total_timeout,
                stop_when_complete_list=getattr(self.adapter, "end2end_output_format", "list") == "list",
                sampling=retry_sampling,
                on_delta=on_delta,
                cancel_event=cancel_event,
            )
            retry_valid = valid_items(retry_raw)
            if len(retry_valid) >= len(first_valid) and retry_valid:
                raw = retry_raw
            elif not first_valid:
                raise RuntimeError("MonkeyOCRv2 ha prodotto un END2END invalido anche dopo il retry nativo")
        elif not first_valid and not native_grounded:
            name = getattr(self.adapter.capabilities, "display_name", self.adapter.adapter_id)
            raise RuntimeError(f"{name} ha prodotto un output END2END non conforme al formato del modello")
        cleaned = []
        for item in self._parse_items(raw):
            bbox = item.get("bbox")
            label = item.get("label") or item.get("category")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or not label:
                continue
            try:
                values = [float(value) for value in bbox]
            except (TypeError, ValueError):
                continue
            if getattr(self.adapter.capabilities, "coordinate_system", "") == "image-pixels":
                values = [
                    values[0] / prepared.width * 1000,
                    values[1] / prepared.height * 1000,
                    values[2] / prepared.width * 1000,
                    values[3] / prepared.height * 1000,
                ]
            if values[0] >= values[2] or values[1] >= values[3]:
                continue
            cleaned.append(
                {
                    "bbox": values,
                    "label": str(label),
                    # "text" oltre a "content": v. nota su "category" per label.
                    "content": str(item.get("content") or item.get("text") or "").strip(),
                }
            )
        return cleaned

    def recognize(
        self,
        image: Image.Image,
        label: str,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        total_timeout: float | None = None,
        hint: str | None = None,
    ) -> str:
        task = "table" if label == "Table" else "text"
        fallback = TABLE_PROMPT if label == "Table" else TEXT_PROMPT
        prompt = _prompt_for(self.adapter, task, label, fallback)
        if task == "table":
            prompt = _with_table_hint(self.adapter, prompt, hint)
        # Una tabella non sta in un budget da blocco di testo: un registro
        # Historic Shipping Index a piena pagina è di 70-80 righe OTSL e a 3072
        # token l'uscita si interrompeva a metà cella (misurato su
        # LSI_17186_015: troncata a "Act 29"). A 1 MP l'immagine costa ~1300
        # token dei 24576 di contesto di MonkeyOCRv2, quindi il budget delle
        # bande (`table_grid`) ci sta comodamente — ma non su ogni adapter.
        raw = self._chat(
            image,
            prompt,
            task=task,
            max_tokens=self._output_budget(TABLE_MAX_TOKENS) if task == "table" else 3072,
            total_timeout=total_timeout,
            sampling=self._sampling_for(task),
            on_delta=on_delta,
            cancel_event=cancel_event,
        )
        return (raw or "").strip()

    def _table_text_to_grid(self, raw: str) -> dict:
        """Testo di una tabella nel formato nativo dell'adapter attivo → griglia
        interna (celle + span). OTSL è il formato di riferimento; un adapter
        HTML passa prima da `otsl.html_to_otsl()`, così la griglia rimane
        sempre lo stesso schema indipendentemente dal modello che l'ha emessa.
        """
        text = (raw or "").strip()
        # PaddleOCR-VL può emettere HTML nel pipeline completo, ma il server
        # vLLM standalone usato da Tabularium può restituire OTSL nativo in
        # risposta allo stesso task. Rileviamo prima il formato effettivo:
        # convertire OTSL come HTML lo azzera completamente.
        if (
            self.adapter.capabilities.table_format == "html"
            and not otsl.looks_like_otsl(text)
        ):
            text = otsl.html_to_otsl(text)
        return otsl.otsl_to_grid(text)

    def table_grid(
        self,
        image: Image.Image,
        *,
        row_bounds: list[int] | None = None,
        rows_per_band: int = DEFAULT_ROWS_PER_BAND,
        header_rows: int = 0,
        max_tokens: int = TABLE_MAX_TOKENS,
        on_delta: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        hint: str | None = None,
    ) -> dict:
        """Riconosce una tabella e restituisce la griglia (celle + span).

        Il riconoscimento va fatto **a bande orizzontali**, non sull'intera
        tabella: un registro Historic Shipping Index a piena pagina è ~8 MP e il modello, dovendo
        comprimerlo, fonde colonne adiacenti (`Flg Reg` e `Blt Gross` finiscono
        in una cella sola). Sulle stesse righe date in bande da ~25 il modello
        tiene le colonne separate e legge correttamente anche le celle di uno o
        due caratteri, che sono quelle su cui l'OCR di riga fallisce.

        `row_bounds` (confini di riga in pixel, dal rilevatore geometrico) evita
        di tagliare una riga a metà fra due bande. Senza, si taglia a intervalli
        regolari e qualche riga di confine può perdersi.
        """
        if getattr(self.adapter, "table_recognition_strategy", None) == "full_crop":
            # Percorso equivalente a MinerUClient.two_step_extract(): una
            # richiesta sul crop completo, che conserva il contesto fra tutte
            # le colonne. Le bande sono solo un fallback sperimentale per
            # altri adapter e qui avrebbero potuto causare la regressione
            # osservata sulle colonne di destra.
            bands = [(0, image.height)]
            row_bounds = None
        else:
            bands = self._band_boxes(image, row_bounds, rows_per_band)
        # Non assumere che la prima riga sia un'intestazione: nei registri
        # Historic Shipping Index spesso è già la prima nave (es. ``Abidjan``). La ripetizione
        # è consentita solo quando l'annotazione dichiara esplicitamente quante
        # righe iniziali costituiscono header.
        header_h = (
            row_bounds[header_rows] - row_bounds[0]
            if row_bounds and header_rows > 0 and len(row_bounds) > header_rows
            else 0
        )
        header = image.crop((0, 0, image.width, header_h)) if header_h > 4 else None
        prompt = _with_table_hint(
            self.adapter,
            _prompt_for(self.adapter, "table", "Table", TABLE_PROMPT),
            hint,
        )

        def recognize_band(item: tuple[int, tuple[int, int]]) -> list[list[dict]]:
            """Riconosce una banda e restituisce le righe già ordinate.

            Le richieste sono indipendenti: su endpoint cloud possono partire
            in parallelo e vLLM le raggruppa nello stesso batch. Il risultato
            viene poi raccolto nell'ordine originale delle bande, quindi non
            cambia né la lettura né la qualità del merge finale.
            """
            index, (top, bottom) = item
            band = image.crop((0, top, image.width, bottom))
            if header is not None and index > 0:
                stitched = Image.new("RGB", (image.width, header.height + band.height), "white")
                stitched.paste(header.convert("RGB"), (0, 0))
                stitched.paste(band.convert("RGB"), (0, header.height))
                band = stitched
            raw = self._chat(
                band,
                prompt,
                task="table",
                max_tokens=max_tokens,
                sampling=self._sampling_for("table"),
                on_delta=on_delta,
                cancel_event=cancel_event,
            )
            grid = self._table_text_to_grid(raw)
            by_row: dict[int, list[dict]] = {}
            for cell in grid.get("cells", []):
                by_row.setdefault(cell["r"], []).append(cell)
            ordered = [
                by_row[r]
                for r in sorted(by_row)
                if any((c.get("text") or "").strip() for c in by_row[r])
            ]
            if header is not None and index > 0 and ordered:
                ordered = ordered[header_rows:]  # header riletti, da non duplicare
            return ordered

        indexed_bands = list(enumerate(bands))
        # Quattro è allineato al profilo Modal (`max-num-seqs=4`). Il locale
        # resta seriale: spesso ha una sola GPU condivisa e non deve pagare
        # quattro picchi di memoria solo per ridurre la latenza di rete.
        if self.is_cloud and len(indexed_bands) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(4, len(indexed_bands))) as pool:
                band_rows = list(pool.map(recognize_band, indexed_bands))
        else:
            band_rows = [recognize_band(item) for item in indexed_bands]

        rows: list[list[dict]] = []
        for ordered in band_rows:
            rows.extend(ordered)

        if not rows:
            return {"rows": 0, "cols": 0, "cells": [], "phantom_cols": []}

        # Bande diverse possono restituire un numero di colonne diverso. Il
        # riferimento è la **moda**, non il massimo: basta una banda mal parsata
        # perché il massimo gonfi l'intera griglia di colonne vuote (osservato:
        # 17 colonne su una tabella che ne ha 9). Le righe più corte si
        # completano con celle vuote, quelle più lunghe si troncano riversando
        # l'eccedenza nell'ultima cella, così non si perde testo.
        widths = Counter(
            max((c["c"] + c["colspan"]) for c in row) for row in rows
        )
        cols = widths.most_common(1)[0][0]

        cells: list[dict] = []
        for r, row in enumerate(rows):
            seen: set[int] = set()
            overflow: list[str] = []
            for cell in sorted(row, key=lambda c: c["c"]):
                if cell["c"] >= cols:
                    text = (cell.get("text") or "").strip()
                    if text:
                        overflow.append(text)
                    continue
                colspan = min(cell["colspan"], cols - cell["c"])
                cells.append({**cell, "r": r, "colspan": colspan})
                seen.update(range(cell["c"], cell["c"] + colspan))
            for c in range(cols):
                if c not in seen:
                    cells.append(
                        {"r": r, "c": c, "rowspan": 1, "colspan": 1, "text": ""}
                    )
            if overflow:
                last = max(
                    (c for c in cells if c["r"] == r), key=lambda c: c["c"]
                )
                last["text"] = " ".join(
                    part for part in [last.get("text", "").strip(), *overflow] if part
                )

        # Sulla griglia già ricucita, non sulle singole bande: l'intestazione
        # sta solo nella prima e le colonne dei dati si contano su tutte.
        return table_headers.repair_flat_header({
            "rows": len(rows),
            "cols": cols,
            "cells": sorted(cells, key=lambda c: (c["r"], c["c"])),
            "phantom_cols": [],
        })

    @staticmethod
    def _band_boxes(
        image: Image.Image, row_bounds: list[int] | None, rows_per_band: int
    ) -> list[tuple[int, int]]:
        """Estremi verticali delle bande da inviare al modello.

        Le bande devono coprire **tutto** il ritaglio. Il rilevatore geometrico
        trova le righe orizzontali della tabella, ma la prima può stare sotto
        l'intestazione (il filetto è disegnato *sotto* i nomi delle colonne) e
        l'ultima sopra l'ultima riga: partire da `hlines[0]` e fermarsi a
        `hlines[-1]` lasciava fuori quelle strisce, che è esattamente il modo in
        cui l'intestazione spariva dalla griglia. Il ritaglio è già la tabella
        annotata dall'utente: nulla al suo interno va scartato in silenzio.
        """
        if row_bounds and len(row_bounds) > 2:
            bounds = sorted(set(int(b) for b in row_bounds))
            bounds = [0] + [b for b in bounds if 0 < b < image.height] + [image.height]
            return [
                (bounds[i], bounds[min(i + rows_per_band, len(bounds) - 1)])
                for i in range(0, len(bounds) - 1, rows_per_band)
            ]
        # Nessun confine noto: bande di altezza fissa, generose ma sotto la
        # soglia oltre la quale il modello comincia a fondere le colonne.
        step = max(200, image.height // max(1, round(image.height / 1200)))
        return [
            (top, min(top + step, image.height))
            for top in range(0, image.height, step)
        ]

    def ping(self, timeout: float | None = None) -> bool:
        # Il protocollo Google non ha `/models`: la disponibilità si prova con
        # la stessa generazione minima di `test_connection`, altrimenti il
        # pre-flight del prefill direbbe «modello non disponibile» sempre.
        if getattr(self.adapter, "chat_protocol", "") == "google-generatecontent":
            return bool(self._test_google_connection(timeout=timeout or self.timeout)["ok"])
        if getattr(self.adapter.capabilities, "serve_backend", "") == "llamaindex-api":
            return bool(self._test_llamaindex_connection(timeout=timeout or self.timeout)["ok"])
        # Il default usa il timeout del client, non un tetto fisso: i tre
        # chiamanti reali (prelabel, blocks, evaluate) lo invocano come
        # pre-flight prima di un'operazione lunga, con il timeout che l'utente
        # ha configurato apposta per il cold start seriale (es. Modal: fino a
        # ~150s). Un tetto fisso a 10s (versione precedente) falliva sempre
        # `ping()` su un endpoint serverless freddo, bloccando il prefill con
        # "modello non disponibile" anche quando l'endpoint funzionava —
        # riprodotto: cold start reale osservato a 142s. Chi vuole un controllo
        # rapido "in apertura pagina" passa un timeout esplicito breve.
        try:
            t = timeout if timeout is not None else self.timeout
            if self.api_key or self.extra_headers:
                resp = requests.get(
                    f"{self.url}/models",
                    headers=self._headers(),
                    timeout=t,
                    allow_redirects=False,
                )
            else:
                resp = requests.get(f"{self.url}/models", timeout=t)
            resp.raise_for_status()
            return True
        except Exception:  # noqa: BLE001
            return False

    def test_connection(self, timeout: float = 10.0) -> dict:
        """Verifica la connessione all'endpoint (locale o cloud) e misura latenza e modelli."""
        # Il protocollo Google non espone `/models` come OpenAI: la prova è una
        # generazione minima, che verifica insieme endpoint, chiave e modello.
        if getattr(self.adapter, "chat_protocol", "") == "google-generatecontent":
            return self._test_google_connection(timeout=timeout)
        if getattr(self.adapter.capabilities, "serve_backend", "") == "llamaindex-api":
            return self._test_llamaindex_connection(timeout=timeout)
        started = time.perf_counter()
        try:
            if self.api_key or self.extra_headers:
                resp = requests.get(
                    f"{self.url}/models",
                    headers=self._headers(),
                    timeout=timeout,
                    allow_redirects=False,
                )
            else:
                resp = requests.get(f"{self.url}/models", timeout=timeout)
            resp.raise_for_status()
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            data = resp.json()
            models: list[str] = []
            if isinstance(data, dict):
                raw_list = data.get("data")
                if isinstance(raw_list, list):
                    models = [
                        str(m.get("id"))
                        for m in raw_list
                        if isinstance(m, dict) and "id" in m
                    ]
                elif "id" in data:
                    models = [str(data["id"])]
            if models and self.model not in models:
                return {
                    "ok": False,
                    "url": self.url,
                    "model": self.model,
                    "models_available": models,
                    "latency_ms": latency_ms,
                    "is_cloud": self.is_cloud,
                    "error": (
                        f"modello '{self.model}' non esposto dall'endpoint; "
                        f"disponibili: {', '.join(models)}"
                    ),
                }
            return {
                "ok": True,
                "url": self.url,
                "model": self.model,
                "models_available": models,
                "latency_ms": latency_ms,
                "is_cloud": self.is_cloud,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            return {
                "ok": False,
                "url": self.url,
                "model": self.model,
                "models_available": [],
                "latency_ms": latency_ms,
                "is_cloud": self.is_cloud,
                "error": str(exc),
            }


    def _test_llamaindex_connection(self, timeout: float = 10.0) -> dict:
        """Validate the user's LlamaCloud key with the cheap versions endpoint."""
        started = time.perf_counter()
        base = _llamaindex_base_url(self.url)

        def result(ok: bool, error: str | None, versions: list[str] | None = None) -> dict:
            return {
                "ok": ok,
                "url": base,
                "model": self.model,
                "models_available": versions or (["ocria-1"] if ok else []),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "is_cloud": True,
                "error": error,
            }

        if not self.api_key:
            return result(False, "API key LlamaIndex mancante")
        try:
            response = requests.get(
                f"{base}/api/v2/parse/versions",
                headers=self._llamaindex_headers(),
                timeout=max(5.0, timeout),
            )
        except requests.RequestException as exc:
            return result(False, f"LlamaCloud non raggiungibile: {exc}")
        if response.status_code in (401, 403):
            return result(False, "API key LlamaIndex rifiutata (HTTP 401/403)")
        if response.status_code >= 400:
            return result(False, f"endpoint versioni LlamaCloud non disponibile (HTTP {response.status_code})")
        try:
            versions = _llamaindex_versions(response.json())
        except ValueError:
            versions = []
        return result(True, None, versions)


    def _test_google_connection(self, timeout: float = 10.0) -> dict:
        """Prova del gateway Google: una generazione minima testo→testo.

        Il gateway non ha un ping economico: `GET /models` è 404, e una richiesta
        malformata (corpo vuoto, `thinkingLevel`, modello inesistente) risponde
        **500 con corpo vuoto**, indistinguibile da un guasto. L'unica risposta
        affidabile è una generazione vera (`maxOutputTokens: 1`), che costa la
        latenza del «pensiero» di Argon (2–20 s): per questo il timeout ha un
        pavimento alto e il risultato è quello che la UI mostra come disponibile.
        """
        started = time.perf_counter()
        payload = {
            "contents": [{"role": "user", "parts": [{"text": "ok"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        }
        headers = self._headers()
        headers["x-opencode-session"] = self._session_id
        headers.setdefault("User-Agent", "tabularium/1.0")
        if self.api_key:
            headers.setdefault("x-goog-api-key", self.api_key)
        endpoint = f"{self._google_base_url()}/models/{self.model}:generateContent"

        def result(ok: bool, error: str | None) -> dict:
            return {
                "ok": ok,
                "url": self.url,
                "model": self.model,
                "models_available": [self.model] if ok else [],
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "is_cloud": self.is_cloud,
                "error": error,
            }

        try:
            response = requests.post(
                endpoint, json=payload, headers=headers, timeout=max(timeout, 30.0)
            )
        except requests.exceptions.RequestException as exc:
            return result(False, str(exc))
        if response.status_code in (401, 403):
            return result(False, "chiave rifiutata dal gateway (HTTP 401)")
        if response.status_code == 200:
            return result(True, None)
        return result(False, f"gateway non disponibile (HTTP {response.status_code})")


def get_inference_config() -> dict:
    """Carica la configurazione di inferenza runtime (da SQLite meta o env)."""
    rows = configstore.overrides()

    enabled_raw = rows.get("inference_enabled")
    enabled = (enabled_raw != "0") if enabled_raw is not None else True

    url = rows.get("inference_url") or config.VLLM_URL
    model = rows.get("inference_model") or config.VLLM_MODEL
    api_key = rows.get("inference_api_key", config.VLLM_API_KEY)
    credential_ref = rows.get("inference_credential_ref")
    if credential_ref:
        from . import vault
        api_key = vault.get(credential_ref)

    extra_headers_raw = rows.get("inference_extra_headers")
    if extra_headers_raw:
        try:
            extra_headers = json.loads(extra_headers_raw)
            if not isinstance(extra_headers, dict):
                extra_headers = {}
        except Exception:
            extra_headers = {}
    else:
        extra_headers = config.VLLM_EXTRA_HEADERS

    timeout_raw = rows.get("inference_timeout")
    timeout = (
        int(timeout_raw)
        if timeout_raw and timeout_raw.isdigit()
        else config.VLLM_TIMEOUT
    )

    max_pixels_raw = rows.get("inference_max_pixels")
    if max_pixels_raw and max_pixels_raw.isdigit():
        max_pixels = int(max_pixels_raw)
    elif "TABULARIUM_VLLM_MAX_PIXELS" in os.environ:
        max_pixels = config.VLLM_MAX_PIXELS
    else:
        # No user override: let model_settings resolve that model's own image
        # recipe instead of applying the legacy MonkeyOCRv2 cap globally.
        max_pixels = None

    # Quale adapter interpreta prompt/formato per l'endpoint servito. Se il
    # vecchio client aveva salvato solo URL/modello, riallineiamo il profilo
    # quando il nome è riconoscibile: altrimenti un endpoint Unlimited può
    # ricevere silenziosamente il prompt/parser di MonkeyOCRv2.
    adapter_id = _adapter_for_endpoint(model, url) or rows.get("inference_adapter_id") or "monkeyocrv2-parsing"
    provider = rows.get("inference_provider") or _provider_for_endpoint(url)
    resource_id = rows.get("inference_resource_id") or None
    provider_credential_ref = rows.get("inference_provider_credential_ref") or None
    source_profile_id: int | None = None

    # Dal DB v10 in poi il profilo attivo è la fonte di verità atomica. La
    # lettura resta tollerante per basi appena migrate o test che non hanno
    # ancora creato il profilo legacy.
    profile = configstore.active_profile()
    if profile:
        source_profile_id = int(profile["id"])
        url = profile["endpoint"]
        model = profile["served_model_name"]
        adapter_id = profile["model_adapter_id"]
        provider = profile["provider"]
        provider_credential_ref = (
            rows.get("inference_provider_credential_ref") or None
            if profile["name"] == "legacy-active"
            else profile["credential_ref"] or None
        )
        try:
            hardware = json.loads(profile["hardware_profile_json"] or "{}")
        except (TypeError, ValueError):
            hardware = {}
        resource_id = (
            rows.get("inference_resource_id") or None
            if profile["name"] == "legacy-active"
            else str(
                hardware.get("resource_id")
                or hardware.get("instance_id")
                or hardware.get("pod_id")
                or ""
            ) or None
        )
        ref = profile["credential_ref"] or ""
        if ref.startswith("env:"):
            api_key = os.environ.get(ref[4:], "")
        elif ref.startswith("vault:"):
            from . import vault
            api_key = vault.get(ref)
        elif ref == "meta:inference_api_key":
            api_key = rows.get("inference_api_key", "")

    # Un modello chiuso via gateway OpenCode non ha una chiave nel vault: la
    # sottoscrizione la conserva già sulla macchina. Vale per ogni lettore
    # (readiness della UI, test, client), quindi si risolve qui una volta sola.
    if not api_key:
        try:
            backend = getattr(get_adapter(adapter_id).capabilities, "serve_backend", "")
        except ValueError:
            backend = ""
        if provider == "opencode" or backend == "opencode-gateway":
            from . import opencode_gateway

            api_key = opencode_gateway.read_api_key()

    native_url = None
    if adapter_id in {"teleocr", "glm-ocr", "mineru2.5"} and provider == "vast":
        try:
            from . import cloud_manager
            tunnel = cloud_manager.get_tunnel_status()
            if tunnel.running and getattr(tunnel, "native_local_port", None):
                native_url = f"http://127.0.0.1:{tunnel.native_local_port}"
        except Exception:  # noqa: BLE001 - legacy/non-Vast profiles have no sidecar
            native_url = None
    elif adapter_id == "mineru2.5" and provider == "local":
        # Local Apple Silicon uses MinerUClient's native mlx-engine gateway on
        # the same port as the server's /v1/models readiness endpoint.
        native_url = url[:-3].rstrip("/") if url.rstrip("/").endswith("/v1") else url.rstrip("/")

    if adapter_id == "llamaindex-ocria-1":
        url = _llamaindex_base_url(url)

    return {
        "enabled": enabled,
        "url": url,
        "model": model,
        "api_key": api_key,
        "extra_headers": extra_headers,
        "timeout": timeout,
        "max_pixels": max_pixels,
        "adapter_id": adapter_id,
        "provider": provider,
        "resource_id": resource_id,
        "provider_credential_ref": provider_credential_ref,
        "source_profile_id": source_profile_id,
        "native_url": native_url,
    }


def _provider_for_endpoint(url: str) -> str:
    value = str(url or "").lower()
    if "opencode.ai" in value:
        return "opencode"
    if "api.cloud.llamaindex.ai" in value or "llamaindex" in value:
        return "llamaindex"
    if ".modal.run" in value:
        return "modal"
    if "runpod" in value:
        return "runpod"
    if any(host in value for host in ("127.0.0.1", "localhost", "0.0.0.0", "::1")):
        return "local"
    return "custom"


def _adapter_for_endpoint(model: str, url: str) -> str | None:
    """Indovina solo adapter con identità esplicita nel nome/URL.

    Non prova a inferire da benchmark o da una risposta generica: il modello
    esposto resta la fonte di verità e `test_connection` verifica che esista.
    """
    value = f"{model} {url}".lower()
    aliases = (
        ("unlimited-ocr", "unlimited-ocr"),
        ("paddleocr-vl", "paddleocr-vl"),
        ("paddleocr_vl", "paddleocr-vl"),
        ("mineru", "mineru2.5"),
        ("dots.mocr", "dots-ocr"),
        ("dots.ocr", "dots-ocr"),
        ("glm-ocr", "glm-ocr"),
        ("deepseek-ocr", "deepseek-ocr"),
        ("qwen3-vl", "qwen3-vl-8b"),
        ("ocria-1", "llamaindex-ocria-1"),
        ("llamaindex", "llamaindex-ocria-1"),
        ("gemini-4-argon", "gemini-argon"),
        ("argon", "gemini-argon"),
        ("monkeyocr", "monkeyocrv2-parsing"),
    )
    for alias, adapter_id in aliases:
        if alias in value:
            return adapter_id
    return None


def save_inference_config(cfg: dict) -> None:
    """Salva la configurazione di inferenza nella tabella meta di SQLite."""
    from ..db import connect  # noqa: PLC0415

    items = []
    if "enabled" in cfg:
        items.append(("inference_enabled", "1" if cfg["enabled"] else "0"))
    if "url" in cfg:
        items.append(("inference_url", str(cfg["url"]).strip()))
    if "model" in cfg:
        items.append(("inference_model", str(cfg["model"]).strip()))
    if "api_key" in cfg:
        from . import vault
        value = str(cfg["api_key"]).strip()
        items.append(("inference_credential_ref", vault.put("inference", value) if value else ""))
        # Rimuove l'eventuale valore legacy in chiaro.
        items.append(("inference_api_key", ""))
    if "extra_headers" in cfg:
        val = cfg["extra_headers"]
        items.append(
            (
                "inference_extra_headers",
                json.dumps(val) if isinstance(val, dict) else "{}",
            )
        )
    if "timeout" in cfg and cfg["timeout"] is not None:
        items.append(("inference_timeout", str(int(cfg["timeout"]))))
    if "max_pixels" in cfg:
        items.append(
            (
                "inference_max_pixels",
                str(int(cfg["max_pixels"])) if cfg["max_pixels"] is not None else "",
            )
        )
    if "adapter_id" in cfg:
        adapter_id = str(cfg["adapter_id"]).strip() or "monkeyocrv2-parsing"
        get_adapter(adapter_id)  # ValueError esplicito se sconosciuto, non salvato a metà
        items.append(("inference_adapter_id", adapter_id))
        # Il tetto pixel globale è un residuo della ricetta MonkeyOCRv2 (1 MP) e
        # non vale per gli altri modelli: cambiando adapter si azzera, così il
        # nuovo modello usa la propria ricetta (o nessun tetto). Un override per
        # modello resta in Impostazioni → Modelli.
        items.append(("inference_max_pixels", ""))
    elif "model" in cfg or "url" in cfg:
        inferred = _adapter_for_endpoint(
            str(cfg.get("model") or ""), str(cfg.get("url") or "")
        )
        if inferred:
            items.append(("inference_adapter_id", inferred))
    identity_changed = any(key in cfg for key in ("url", "model", "adapter_id"))
    if identity_changed:
        provider = str(cfg.get("provider") or _provider_for_endpoint(str(cfg.get("url") or ""))).strip()
        # `compute_profiles.provider` ha un CHECK con l'elenco dei provider
        # fisici; un endpoint esterno come il gateway OpenCode è «custom» nel
        # profilo (resta remoto, quindi `is_cloud` è vero) anche se `provider`
        # in memoria può portare l'etichetta più specifica.
        if provider not in _PROFILE_PROVIDERS:
            provider = "custom"
        items.append(("inference_provider", provider))
        items.append(("inference_resource_id", str(cfg.get("resource_id") or "").strip()))
        items.append((
            "inference_provider_credential_ref",
            str(cfg.get("provider_credential_ref") or "").strip(),
        ))

    profile_update = None
    deactivate_profile = False
    profile = configstore.active_profile()
    if profile and profile["name"] == "legacy-active" and identity_changed:
        profile_update = {
            "id": profile["id"],
            "endpoint": str(cfg.get("url", profile["endpoint"])).strip(),
            "served_model_name": str(cfg.get("model", profile["served_model_name"])).strip(),
            "model_adapter_id": str(
                cfg.get("adapter_id")
                or _adapter_for_endpoint(
                    str(cfg.get("model", profile["served_model_name"])),
                    str(cfg.get("url", profile["endpoint"])),
                )
                or profile["model_adapter_id"]
            ),
            "provider": cfg.get("provider", profile["provider"]),
            "hardware_profile_json": json.dumps(
                {"resource_id": str(cfg.get("resource_id") or "").strip()}
            ),
            # Empty string = "the key was removed"; None = "leave it as it is".
            "credential_ref": (
                "vault:inference" if str(cfg.get("api_key") or "").strip()
                else "" if "api_key" in cfg
                else None
            ),
        }
    elif profile and identity_changed:
        # A direct selection replaces the saved profile: leaving it active would make
        # `get_inference_config` silently keep using the old URL.
        deactivate_profile = True
    configstore.save(items, profile_update=profile_update, deactivate_profile=deactivate_profile)


def get_vllm_client(
    url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
    timeout: int | None = None,
) -> VllmClient:
    """Crea un VllmClient con la configurazione runtime persistita o gli override forniti."""
    cfg = get_inference_config()
    try:
        adapter = get_adapter(cfg.get("adapter_id") or "monkeyocrv2-parsing")
    except ValueError:
        # Adapter salvato ma non più registrato (es. rinominato): meglio
        # ripiegare su MonkeyOCRv2 che rifiutare la richiesta di inferenza.
        adapter = MonkeyOCRv2ParsingAdapter()
    try:
        resolved_key = api_key if api_key is not None else cfg.get("api_key")
        # I modelli chiusi via gateway OpenCode non hanno una chiave nel vault
        # al primo uso: opencode la conserva già sulla macchina, quindi la
        # prendiamo da lì invece di chiedere di incollarla due volte.
        if not resolved_key and (
            cfg.get("provider") == "opencode"
            or getattr(adapter.capabilities, "serve_backend", "") == "opencode-gateway"
        ):
            from . import opencode_gateway

            resolved_key = opencode_gateway.read_api_key() or None
        kwargs = {
            "url": url or cfg["url"],
            "model": model or cfg["model"],
            "api_key": resolved_key,
            "extra_headers": (
                extra_headers if extra_headers is not None else cfg.get("extra_headers")
            ),
            "timeout": timeout if timeout is not None else cfg.get("timeout", 180),
            "adapter": adapter,
            "provider": cfg.get("provider"),
            "native_url": cfg.get("native_url"),
        }
        if cfg.get("max_pixels") is not None:
            kwargs["max_pixels"] = cfg["max_pixels"]
        return VllmClient(**kwargs)
    except TypeError:
        try:
            return VllmClient(url=url or cfg["url"], model=model or cfg["model"], adapter=adapter)
        except TypeError:
            return VllmClient()
