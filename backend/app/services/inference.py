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
from collections import Counter
from collections.abc import Callable

import requests
from PIL import Image

from .. import config
from . import otsl
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
LAYOUT_MIN_PIXELS = 1_003_520

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


class _OuterListScanner:
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


def _tolerant_items(text: str) -> list[dict]:
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
        # Default MonkeyOCRv2: i chiamanti esistenti (test compresi) costruiscono
        # `VllmClient` senza adapter e si aspettano il comportamento storico.
        self.adapter: ModelAdapter = adapter or MonkeyOCRv2ParsingAdapter()
        # Equivalente di `MOCR2_MAX_PIXELS`: vale per ogni chiamata al modello.
        # 0 o negativo = nessun tetto, come non impostare la env ufficiale.
        cap = config.VLLM_MAX_PIXELS if max_pixels is _MAX_PIXELS_DEFAULT else max_pixels
        self.max_pixels: int | None = cap if (cap or 0) > 0 else None
        self.last_trace: dict = {}
        self.last_text = ""

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.extra_headers:
            headers.update(self.extra_headers)
        return headers

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
        prepared = _fit_pixels(image, min_pixels=min_pixels, max_pixels=cap)
        prepared.convert("RGB").save(buf, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        # Tutti gli endpoint OpenAI-compatible, incluso Modal, usano lo
        # streaming: la UI può mostrare i delta durante il decoding. La
        # compatibilità Modal è garantita drenando la risposta fino a [DONE]
        # anche quando `_OuterListScanner` ha già trovato la lista completa.
        stream_response = True
        content = [
            {"type": "image_url", "image_url": {"url": data_uri}},
            {"type": "text", "text": prompt},
        ]
        if getattr(self.adapter, "multimodal_content_order", "image-text") == "text-image":
            content.reverse()
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": stream_response,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        }
        if stream_response:
            payload["stream_options"] = {"include_usage": True}
        # Alcuni modelli richiedono penalità anti-ripetizione specifiche lato
        # richiesta (es. MinerU2.5, abbinate al `--logits-processors` server:
        # v. `MinerU2_5Adapter.sampling_for`). Non tocca `temperature`/
        # `max_tokens`/`stream`: quelle chiavi non compaiono in `sampling`.
        if sampling:
            payload.update(sampling)
        request_overrides = getattr(self.adapter, "request_overrides", None)
        if callable(request_overrides):
            payload.update(request_overrides(task) or {})
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
                                first_token_at = time.perf_counter()
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
                    "max_tokens": max_tokens,
                    "chars": len(text),
                    "complete_list": scanner.complete if stop_when_complete_list else None,
                }
                if stop_when_complete_list and not scanner.complete:
                    raise RuntimeError(
                        "output END2END incompleto "
                        f"(finish={finish_reason}, chars={len(text)}, max_tokens={max_tokens})"
                    )
                return text
            except Exception as exc:  # noqa: BLE001
                last = exc
                # Ritentare una generazione già iniziata moltiplica il costo e
                # può lasciare richieste orfane. I retry servono solo a errori
                # di connessione avvenuti prima del primo token.
                if chunks or isinstance(exc, TimeoutError):
                    break
        raise RuntimeError(f"vLLM non raggiungibile ({self.url}): {last}")

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

    def _sampling_for(self, task: str) -> dict | None:
        """Override di sampling per task, se l'adapter ne dichiara (es.
        le penalità anti-ripetizione richieste da MinerU2.5)."""
        sampling_for = getattr(self.adapter, "sampling_for", None)
        return sampling_for(task) if callable(sampling_for) else None

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
        # Unlimited's grounded protocol has a deliberately smaller server
        # budget; generic/end-to-end document parsing remains generous.
        if getattr(self.adapter, "adapter_id", None) == "unlimited-ocr":
            end2end_tokens = min(max_tokens, getattr(self.adapter, "end2end_max_tokens", max_tokens))
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
                and item.get("label")
            ]

        # Il repo ufficiale riconosce il loop di token e ritenta con un
        # campionamento leggermente meno rigido. Applichiamo la stessa idea al
        # client HTTP, ma solo se il primo output è patologico: un END2END
        # normale resta una sola chiamata. Questo evita di salvare bbox come
        # [509, 21, 960, 998, 998, ...].
        first_valid = valid_items(raw)
        # Grounded-markdown (Unlimited-OCR) is a native protocol, not a JSON
        # list. An empty parsed bbox list must not trigger the generic
        # MonkeyOCR retry: the raw response is still the authoritative output
        # to inspect and a second 8 GB generation can exceed the request
        # budget without adding evidence.
        native_grounded = getattr(self.adapter, "end2end_output_format", "list") == "grounded-markdown"
        if not native_grounded and (not first_valid or _should_retry_repeat_output(raw)):
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
                raise RuntimeError(
                    "MonkeyOCRv2 ha prodotto un END2END invalido anche dopo il retry anti-ripetizione"
                )
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
    ) -> str:
        task = "table" if label == "Table" else "text"
        fallback = TABLE_PROMPT if label == "Table" else TEXT_PROMPT
        prompt = _prompt_for(self.adapter, task, label, fallback)
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
        prompt = _prompt_for(self.adapter, "table", "Table", TABLE_PROMPT)

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

        return {
            "rows": len(rows),
            "cols": cols,
            "cells": sorted(cells, key=lambda c: (c["r"], c["c"])),
            "phantom_cols": [],
        }

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


def get_inference_config() -> dict:
    """Carica la configurazione di inferenza runtime (da SQLite meta o env)."""
    from ..db import connect  # noqa: PLC0415

    try:
        with connect() as conn:
            rows = dict(
                conn.execute(
                    "SELECT key, value FROM meta WHERE key LIKE 'inference_%'"
                ).fetchall()
            )
    except Exception:
        rows = {}

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
    max_pixels = (
        int(max_pixels_raw)
        if max_pixels_raw and max_pixels_raw.isdigit()
        else config.VLLM_MAX_PIXELS
    )

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
    try:
        with connect() as conn:
            profile = conn.execute("SELECT * FROM compute_profiles WHERE active=1 LIMIT 1").fetchone()
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
    except Exception:
        profile = None

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
    }


def _provider_for_endpoint(url: str) -> str:
    value = str(url or "").lower()
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
    elif "model" in cfg or "url" in cfg:
        inferred = _adapter_for_endpoint(
            str(cfg.get("model") or ""), str(cfg.get("url") or "")
        )
        if inferred:
            items.append(("inference_adapter_id", inferred))
    identity_changed = any(key in cfg for key in ("url", "model", "adapter_id"))
    if identity_changed:
        provider = str(cfg.get("provider") or _provider_for_endpoint(str(cfg.get("url") or ""))).strip()
        items.append(("inference_provider", provider))
        items.append(("inference_resource_id", str(cfg.get("resource_id") or "").strip()))
        items.append((
            "inference_provider_credential_ref",
            str(cfg.get("provider_credential_ref") or "").strip(),
        ))

    with connect() as conn:
        for key, value in items:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        # Compatibilità con la vecchia API: se è attivo il profilo legacy,
        # aggiorniamo il record atomico nella stessa transazione. Un update
        # parziale conserva gli altri campi del profilo, quindi non crea più
        # la combinazione URL/adapter/modello incoerente della vecchia UI.
        try:
            active = conn.execute("SELECT * FROM compute_profiles WHERE active=1 LIMIT 1").fetchone()
            if active and active["name"] == "legacy-active" and identity_changed:
                next_url = str(cfg.get("url", active["endpoint"])).strip()
                next_model = str(cfg.get("model", active["served_model_name"])).strip()
                next_adapter = str(cfg.get("adapter_id") or _adapter_for_endpoint(next_model, next_url) or active["model_adapter_id"])
                conn.execute(
                    "UPDATE compute_profiles SET endpoint=?, served_model_name=?, model_adapter_id=?, provider=?, hardware_profile_json=?, credential_ref=CASE WHEN ? THEN 'vault:inference' WHEN ? THEN '' ELSE credential_ref END, updated_at=datetime('now') WHERE id=?",
                    (
                        next_url,
                        next_model,
                        next_adapter,
                        provider,
                        json.dumps({"resource_id": str(cfg.get("resource_id") or "").strip()}),
                        bool(str(cfg.get("api_key") or "").strip()),
                        "api_key" in cfg,
                        active["id"],
                    ),
                )
            elif active and identity_changed:
                # Una selezione diretta (server locale, Modal, RunPod, Vast o
                # endpoint manuale) sostituisce il profilo salvato. Lasciarlo
                # attivo farebbe sì che get_inference_config ignorasse la nuova
                # scelta e continuasse a usare silenziosamente il vecchio URL.
                conn.execute("UPDATE compute_profiles SET active=0, updated_at=datetime('now')")
        except sqlite3.OperationalError:
            # Basi non ancora migrate alla v10 restano compatibili.
            pass


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
        return VllmClient(
            url=url or cfg["url"],
            model=model or cfg["model"],
            api_key=api_key if api_key is not None else cfg.get("api_key"),
            extra_headers=(
                extra_headers if extra_headers is not None else cfg.get("extra_headers")
            ),
            timeout=timeout if timeout is not None else cfg.get("timeout", 180),
            adapter=adapter,
            max_pixels=cfg.get("max_pixels"),
            provider=cfg.get("provider"),
        )
    except TypeError:
        try:
            return VllmClient(url=url or cfg["url"], model=model or cfg["model"], adapter=adapter)
        except TypeError:
            return VllmClient()
