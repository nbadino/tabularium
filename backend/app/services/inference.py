"""Client inferenza via vLLM (endpoint OpenAI-compatibile).

Usato da valutazione (M6) e playground: layout completo, riconoscimento testo,
riconoscimento tabelle (OTSL). I bbox emessi dal modello sono in scala 0-1000.
"""
from __future__ import annotations

import ast
import base64
import io
import json
import re
import time
from collections import Counter

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
    """`adapter.prompt_for` con ripiego sulla costante storica.

    Stesso pattern `or fallback` già usato in `dataset_builder.py`. Un
    adapter nuovo può non avere ancora quel task implementato
    (`NotImplementedError`, v. gli stub in `model_adapters.py`): in quel caso
    come quando l'adapter torna `None` si ricade sul prompt storico di
    MonkeyOCRv2, invece di propagare l'errore fino alla richiesta HTTP.
    """
    try:
        return adapter.prompt_for(task, label) or fallback
    except NotImplementedError:
        return fallback

# Semantica identica a `load_image` di `parsing/core_runner.py` (repo ufficiale):
#   - `min_pixels` INGRANDISCE le immagini piccole, non le riduce. Il pipeline
#     ufficiale passa 1003520 alla chiamata di layout, come *minimo*.
#   - un tetto superiore esiste solo se richiesto: là è la env `MOCR2_MAX_PIXELS`,
#     qui `TABULARIUM_VLLM_MAX_PIXELS`. Di default nessuno dei due riduce nulla.
# Attenzione a non confondere questo con `max_pixels 1003520` degli iperparametri
# di *training* (AGENTS.md §2.6): sono due cose diverse, e usare quel valore come
# tetto in inferenza non è ciò che fa il codice ufficiale.
LAYOUT_MIN_PIXELS = 1_003_520

# Tetto applicato **alla sola chiamata di layout**. Il codice ufficiale non ne
# impone uno, ma è pensato per pagine di dimensioni ordinarie: una scansione
# d'archivio Historic Shipping Index è 11.3 MP e a quella taglia il layout collassa. Misurato
# sulla stessa pagina (LSI_17186_015), a parità di tutto il resto:
#
#     11.3 MP → 2 blocchi sovrapposti e inutili
#      6.0 MP → 16 blocchi, frammentato
#      4.0 MP → 137 blocchi, esploso a livello di cella
#      2.0 MP → 5 blocchi: Title, numero, data, corpo   ← scelto
#      1.0 MP → 4 blocchi, corretto ma meno granulare
#
# Il tetto NON si applica al riconoscimento di testo e tabelle: lì il ritaglio
# è già piccolo e ridurlo perderebbe i caratteri. Override con
# `TABULARIUM_VLLM_MAX_PIXELS`, equivalente di `MOCR2_MAX_PIXELS` del repo ufficiale.
LAYOUT_MAX_PIXELS = 2_000_000

# Righe per banda nel riconoscimento tabella. Misurato su LSI_17186_015, tabella
# di 78 righe geometriche: con bande da 15 righe il modello ne restituisce 76 su
# 8 colonne stabili, con bande da 25 ne restituisce 59. Bande più corte costano
# più chiamate ma non perdono righe.
DEFAULT_ROWS_PER_BAND = 15

# Il checkpoint supporta 40.960 token, ma su una RTX 4060 la cache KV disponibile
# consente in pratica un limite operativo di 24.576 (configurato in
# `scripts/serve_model.sh`). Il precedente 32.768 poteva comunque superare il
# contesto effettivo immagine + prompt + output e lasciare richieste orfane.
# 20.480 è un tetto di output, non il contesto totale: il parser rifiuta sempre
# una lista troncata. Sulla pagina indice più lunga osservata 16.384 non bastano.
END2END_MAX_TOKENS = 20_480


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
    ) -> None:
        self.url = (url or config.VLLM_URL).rstrip("/")
        self.model = model or config.VLLM_MODEL
        self.api_key = api_key if api_key is not None else config.VLLM_API_KEY
        self.extra_headers = extra_headers if extra_headers is not None else config.VLLM_EXTRA_HEADERS
        self.timeout = timeout
        self.max_retries = max_retries
        # Default MonkeyOCRv2: i chiamanti esistenti (test compresi) costruiscono
        # `VllmClient` senza adapter e si aspettano il comportamento storico.
        self.adapter: ModelAdapter = adapter or MonkeyOCRv2ParsingAdapter()
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
        """Indica se l'endpoint punta a un server remoto/cloud o alla macchina locale."""
        url_lower = self.url.lower()
        return not any(
            loc in url_lower
            for loc in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "local")
        )

    def _chat(
        self,
        image: Image.Image,
        prompt: str,
        max_tokens: int = 4096,
        min_pixels: int | None = None,
        total_timeout: float | None = None,
        stop_when_complete_list: bool = False,
    ) -> str:
        buf = io.BytesIO()
        prepared = _fit_pixels(
            image, min_pixels=min_pixels, max_pixels=config.VLLM_MAX_PIXELS
        )
        prepared.convert("RGB").save(buf, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            first_token_at: float | None = None
            chunks: list[str] = []
            usage: dict = {}
            finish_reason: str | None = None
            scanner = _OuterListScanner()
            try:
                with requests.post(
                    f"{self.url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                    timeout=(10, self.timeout),
                    stream=True,
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines(decode_unicode=True):
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
                        chunks.append(delta)
                        if stop_when_complete_list and scanner.feed(delta):
                            finish_reason = "complete_list"
                            break
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

    def layout(self, image: Image.Image) -> list[dict]:
        # `min_pixels=1003520` è ciò che passa `get_layout` ufficiale; il tetto
        # è nostro e riguarda solo questa chiamata (v. LAYOUT_MAX_PIXELS).
        prepared = _fit_pixels(
            image,
            min_pixels=LAYOUT_MIN_PIXELS,
            max_pixels=config.VLLM_MAX_PIXELS or LAYOUT_MAX_PIXELS,
        )
        prompt = _prompt_for(self.adapter, "layout", None, LAYOUT_PROMPT)
        raw = self._chat(prepared, prompt, max_tokens=4096)
        cleaned = []
        for item in _tolerant_items(raw):
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
    ) -> list[dict]:
        """Pagina intera: bbox, label e contenuto in una sola generazione.

        È il percorso ufficiale ``ALL_PROMPT['END2END']``. Come per il layout
        applichiamo il tetto sperimentale di 2 MP che evita la frammentazione
        osservata sulle scansioni Historic Shipping Index ad alta risoluzione.
        """
        prepared = _fit_pixels(
            image,
            min_pixels=LAYOUT_MIN_PIXELS,
            max_pixels=max_pixels or config.VLLM_MAX_PIXELS or LAYOUT_MAX_PIXELS,
        )
        prompt = _prompt_for(self.adapter, "end2end", None, END2END_PROMPT)
        raw = self._chat(
            prepared,
            prompt,
            max_tokens=max_tokens,
            total_timeout=total_timeout,
            stop_when_complete_list=True,
        )
        cleaned = []
        for item in _tolerant_items(raw):
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

    def recognize(self, image: Image.Image, label: str) -> str:
        task = "table" if label == "Table" else "text"
        fallback = TABLE_PROMPT if label == "Table" else TEXT_PROMPT
        prompt = _prompt_for(self.adapter, task, label, fallback)
        raw = self._chat(image, prompt, max_tokens=3072)
        return (raw or "").strip()

    def _table_text_to_grid(self, raw: str) -> dict:
        """Testo di una tabella nel formato nativo dell'adapter attivo → griglia
        interna (celle + span). OTSL è il formato di riferimento; un adapter
        HTML passa prima da `otsl.html_to_otsl()`, così la griglia rimane
        sempre lo stesso schema indipendentemente dal modello che l'ha emessa.
        """
        text = (raw or "").strip()
        if self.adapter.capabilities.table_format == "html":
            text = otsl.html_to_otsl(text)
        return otsl.otsl_to_grid(text)

    def table_grid(
        self,
        image: Image.Image,
        *,
        row_bounds: list[int] | None = None,
        rows_per_band: int = DEFAULT_ROWS_PER_BAND,
        header_rows: int = 0,
        max_tokens: int = 8192,
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

        rows: list[list[dict]] = []
        for index, (top, bottom) in enumerate(bands):
            band = image.crop((0, top, image.width, bottom))
            if header is not None and index > 0:
                stitched = Image.new("RGB", (image.width, header.height + band.height), "white")
                stitched.paste(header.convert("RGB"), (0, 0))
                stitched.paste(band.convert("RGB"), (0, header.height))
                band = stitched
            raw = self._chat(band, prompt, max_tokens=max_tokens)
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
        """Estremi verticali delle bande da inviare al modello."""
        if row_bounds and len(row_bounds) > 2:
            bounds = sorted(set(int(b) for b in row_bounds))
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
        # Usa il timeout del client, non uno fisso: chi interroga la
        # disponibilità all'apertura di una pagina lo imposta a pochi secondi e
        # non può restare appeso venti secondi se il server è spento.
        try:
            t = timeout if timeout is not None else min(self.timeout, 10)
            if self.api_key or self.extra_headers:
                resp = requests.get(
                    f"{self.url}/models",
                    headers=self._headers(),
                    timeout=t,
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

    # Quale adapter interpreta prompt/formato tabella per l'endpoint servito.
    # Default MonkeyOCRv2: nessuna installazione esistente deve ricalibrare
    # nulla per continuare a funzionare come prima di questa configurazione.
    adapter_id = rows.get("inference_adapter_id") or "monkeyocrv2-parsing"

    return {
        "enabled": enabled,
        "url": url,
        "model": model,
        "api_key": api_key,
        "extra_headers": extra_headers,
        "timeout": timeout,
        "max_pixels": max_pixels,
        "adapter_id": adapter_id,
    }


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
        items.append(("inference_api_key", str(cfg["api_key"]).strip()))
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

    with connect() as conn:
        for key, value in items:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )


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
        )
    except TypeError:
        try:
            return VllmClient(url=url or cfg["url"], model=model or cfg["model"], adapter=adapter)
        except TypeError:
            return VllmClient()


