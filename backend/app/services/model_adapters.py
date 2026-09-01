"""Contratti per profili di dominio e adapter di modelli.

Il database conserva annotazioni ricche e indipendenti dal modello. Un adapter
traduce quello schema nei prompt, nei formati e nei vincoli del modello scelto.
MonkeyOCRv2 è il primo adapter, non il contratto globale dell'applicazione.

Ogni adapter dichiara anche una *ricetta* di installazione (repo Hugging Face,
toolchain di training, backend di serving): è quanto serve al registro modelli
(`model_registry.py`) per scaricare i pesi e, nelle fasi successive, per
servirli e addestrarli. Per i modelli aggiunti nella prima fase multi-modello
(oltre a MonkeyOCRv2) solo la ricetta di download è verificata sul repo
ufficiale; `serve_command` resta `None` finché non arriva il relativo lavoro di
serving (vedi `docs/OCR_MODEL_ALTERNATIVES.md` e il piano multi-modello).
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ModelCapabilities:
    """Capacità dichiarate da un backend di training/inferenza."""

    adapter_id: str
    display_name: str
    tasks: tuple[str, ...]
    coordinate_system: str
    table_format: str | None
    training_types: tuple[str, ...]
    inference_modes: tuple[str, ...]
    hardware: tuple[str, ...]
    geometry: tuple[str, ...] = ("bbox",)
    reading_order: bool = True
    languages: tuple[str, ...] = ("en",)
    # --- ricetta di installazione/training (nuovo) --------------------------
    hf_repo: str = ""
    hf_revision: str | None = None
    approx_size_gb: float | None = None
    license_note: str = ""
    # id logico di toolchain: "ms-swift" | "llama-factory" | "unsloth" |
    # "community" | "none" (nessun training ancora supportato).
    train_toolchain: str = "none"
    # "vllm-openai" (endpoint OpenAI-compatibile via `vllm serve` diretto) |
    # "docker-vllm-openai" (stesso endpoint, ma lanciato dentro un'immagine
    # Docker dedicata perché l'architettura non è nella wheel pip stabile) |
    # "custom" | "none".
    serve_backend: str = "none"
    # Nome che il client OpenAI-compatibile deve passare nel campo "model"
    # della richiesta (`--served-model-name` di vLLM). `None` = deciso altrove
    # (MonkeyOCRv2 lo configura oggi da UI, storicamente "MonkeyOCRv2").
    served_model_name: str | None = None
    # Repo Hugging Face del draft per lo speculative decoding, quando il
    # modello ne pubblica uno (MonkeyOCRv2: DFlash). Vuoto = nessun draft.
    draft_hf_repo: str = ""
    # Immagine Docker richiesta dal serving, quando l'architettura non è nella
    # wheel pip stabile (`serve_backend="docker-vllm-openai"`). Dichiararla qui
    # invece di dedurla dall'argv permette a `serve_manager` di scaricarla in
    # una fase esplicita, invece di lasciare `docker run` a farlo in silenzio.
    serve_image: str = ""
    # Contesto con cui l'adapter serve il modello: il `--max-model-len` del
    # `serve_command` se lo impone, altrimenti il default del checkpoint. Serve
    # al client per non chiedere più token in uscita di quanti ne esistano: una
    # richiesta con `max_tokens` oltre il contesto viene rifiutata dal server
    # con 400, non troncata.
    max_model_len: int = 16384
    # Maturità del percorso prodotto: un modello può essere scaricabile senza
    # che inferenza/export/training siano ancora verificati insieme.
    maturity: str = "catalog"


class ModelAdapter(Protocol):
    adapter_id: str
    capabilities: ModelCapabilities

    def prompt_for(self, task: str, label: str | None = None) -> str | None: ...

    def serialize_target(self, task: str, value: object) -> str: ...

    def serve_command(self, model_path: str, port: int) -> list[str] | None: ...

    def request_overrides(self, task: str) -> dict: ...


class MonkeyOCRv2ParsingAdapter:
    """Adapter per il formato ufficiale MonkeyOCRv2-Parsing/ms-swift."""

    adapter_id = "monkeyocrv2-parsing"
    # Tetto ufficiale: `parsing/parse.py --max-pixels` (default 1003520) viene
    # propagato come `MOCR2_MAX_PIXELS` a ogni chiamata, end2end inclusa. Sul
    # documento archivistico è anche ciò che impedisce al decoder di andare in
    # loop sulle coordinate quando riceve gli 11 MP originali.
    end2end_max_pixels = 1_003_520
    end2end_max_tokens = 12_288
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="MonkeyOCRv2-Parsing",
        tasks=("layout", "text", "table", "formula"),
        coordinate_system="normalized-0-1000",
        table_format="otsl",
        training_types=("lora", "full"),
        inference_modes=("vllm", "cpu"),
        hardware=("cuda", "cpu-inference"),
        geometry=("bbox", "polygon"),
        reading_order=True,
        languages=("en", "multilingual-partial"),
        hf_repo="zenosai/MonkeyOCRv2-B-Parsing",
        approx_size_gb=1.5,
        license_note="",
        train_toolchain="ms-swift",
        serve_backend="vllm-openai",
        # `scripts/serve_model.sh` delega al `serve.py` del repo ufficiale, che
        # registra il modello su /v1/models come "MonkeyOCRv2" (lo stesso
        # default di `config.VLLM_MODEL`). Senza questo campo l'avvio dal
        # registro modelli salvava `adapter_id` come nome richiesto e ogni
        # chiamata falliva con "modello 'monkeyocrv2-parsing' non esposto".
        served_model_name="MonkeyOCRv2",
        max_model_len=24576,  # v. scripts/serve_model.sh
        # README ufficiale (news 2026.07.24): DFlash abilita il serving vLLM
        # con speculative decoding, "up to 2x faster inference". Supportato
        # solo per la variante B-Parsing.
        draft_hf_repo="zenosai/MonkeyOCRv2-B-Parsing-DFlash",
        maturity="supported",
    )

    _PROMPTS = {
        "layout": "Please output the categories and coordinates of the document elements in reading order.",
        "text": "Please output the text content from the image.",
        "table": "Please extract the table from the image and represent it in OTSL format.",
        "formula": "Please write out the expression of the formula in the image using LaTeX format.",
        "end2end": (
            "List the document elements in reading order, including their categories, "
            "coordinates, and the content of each element."
        ),
    }

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        if task == "text" and label in {"Column", "Picture"}:
            return None
        return self._PROMPTS.get(task)

    def serialize_target(self, task: str, value: object) -> str:
        if task == "layout":
            import json

            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if task == "formula":
            text = str(value).strip()
            return text if text.startswith("$") else f"${text}$"
        return str(value)

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        # Il repo ufficiale espone un wrapper `serve.py` proprio (non il
        # semplice `vllm serve`), da eseguire con l'env dedicato (gcc-13,
        # NVCC_PREPEND_FLAGS per il JIT di flash-attn) dalla cartella
        # `parsing/` del checkout vendored. Invece di reimplementare quelle
        # variabili qui, deleghiamo allo script già collaudato: un adapter
        # può sempre restituire l'invocazione di un wrapper invece del
        # comando nudo, `serve_manager.py` resta generico (`Popen(argv)`).
        script = Path(__file__).resolve().parents[3] / "scripts" / "serve_model.sh"
        return [str(script), model_path, str(port)]


class _StubAdapter:
    """Adapter di sola ricetta: capacità dichiarate + download, niente ancora
    di prompt/serving/training reali. `prompt_for`/`serialize_target` alzano
    un errore esplicito se qualcosa tenta di usarli prima che l'adapter sia
    completato — meglio di un prompt sbagliato passato in silenzio a un
    modello che non lo capisce."""

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': prompt non ancora implementati "
            "(solo download disponibile in questa fase)"
        )

    def serialize_target(self, task: str, value: object) -> str:
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': training non ancora implementato"
        )

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return None

    def request_overrides(self, task: str) -> dict:
        return {}


class MinerU2_5Adapter(_StubAdapter):
    """opendatalab/MinerU2.5 — due stadi (layout su downscale + contenuto su
    crop nativo), OTSL nativo per le tabelle: l'architettura più vicina a
    quella già in uso per MonkeyOCRv2, al punto che il suo protocollo si
    incastra nel nostro `layout()` + `recognize()`/`table_grid()` esistenti.

    `serve_command` verificato sul runtime MinerU 3.4.5 e
    github.com/opendatalab/mineru-vl-utils (agosto 2026): richiede
    `--logits-processors
    mineru_vl_utils:MinerULogitsProcessor` per il `no_repeat_ngram_size` che
    evita generazioni ripetute — attivato lato server dal flag, lato client
    dalle penalità in `sampling_for`. Il pacchetto Python va installato
    **senza** l'extra `[vllm]`: la matrice di compatibilità pubblicata dichiara
    vLLM <0.22.0; la template Modal usa 0.21.0 — il logits
    processor sta nel pacchetto base, non nell'extra (v.
    `scripts/cloud/modal_mineru.py`). L'extra `[vllm]` dichiara vLLM
    `<0.22.0`; la template usa quindi 0.21.0. Nota anche per chi volesse
    installare `mineru-vl-utils` nell'ambiente di Tabularium stesso (non
    fatto: nessuna release supporta ancora Python 3.14, la versione qui in
    uso) — è il motivo per cui il protocollo è reimplementato sotto invece di
    dipendere dal client ufficiale.

    `hf_repo` aggiornato all'ultimo checkpoint "Pro" della stessa famiglia
    1.2B (stesso comando di serve, solo repo diverso); l'esempio verificato
    dalla guida usa `opendatalab/MinerU2.5-2509-1.2B`.

    `prompt_for`/`parse_layout`/`sampling_for` reimplementano (non wrappano)
    il protocollo di `mineru_vl_utils.MinerUClient.two_step_extract()`,
    verificato leggendo `mineru_client.py`/`structs.py` sul repo ufficiale:
      - prompt "Layout Detection:" → blocchi `<|box_start|>x1 y1 x2 y2
        <|box_end|><|ref_start|>tipo<|ref_end|>[rotazione]testo`, coordinate
        già in scala 0–1000 (lo stesso schema di MonkeyOCRv2: il client
        ufficiale le normalizza a [0,1] dividendo per 1000, qui si tengono
        grezze). Il testo di coda è quasi sempre vuoto per blocchi non-testo:
        la ricognizione vera arriva dal secondo giro.
      - prompt "Table Recognition:"/"Text Recognition:"/"Formula Recognition:"
        per il secondo giro, con penalità anti-ripetizione dedicate.
      - il client ufficiale salta il secondo giro per liste/formule/immagini
        (resta il testo di coda del layout, spesso vuoto); qui non serve
        replicarlo: il prefill di Tabularium chiama `recognize()` solo su
        richiesta esplicita (tabelle), il resto lo trascrive l'utente.
    """

    adapter_id = "mineru2.5"
    # Il client ufficiale MinerUClient prepara il layout a 1036x1036
    # esattamente (non a un tetto di pixel con aspect ratio conservato). Il
    # checkpoint si aspetta questa rappresentazione; lasciarla cambiare in
    # base alla risoluzione della scansione altera il contesto visivo.
    official_layout_size = (1036, 1036)
    # Il client ufficiale riconosce il crop della tabella intero. Le bande
    # restano un fallback sperimentale di Tabularium per gli adapter per cui è
    # stato misurato; non si applicano a MinerU senza prova equivalente.
    table_recognition_strategy = "full_crop"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="MinerU2.5",
        tasks=("layout", "text", "table", "formula"),
        coordinate_system="normalized-0-1000",
        table_format="otsl",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh", "multilingual-partial"),
        hf_repo="opendatalab/MinerU2.5-Pro-2605-1.2B",
        approx_size_gb=2.5,
        license_note="",
        train_toolchain="none",
        serve_backend="vllm-openai",
        served_model_name="mineru2.5",
    )

    _PROMPTS = {
        "layout": "\nLayout Detection:",
        "text": "\nText Recognition:",
        "table": "\nTable Recognition:",
        "formula": "\nFormula Recognition:",
    }

    # Mappa le categorie di mineru-vl-utils (BLOCK_TYPES, minuscole) sulla
    # tassonomia pubblica di Tabularium; ciò che non compare qui ricade su
    # "Text" più a valle in `model_prelabel_pages` (comportamento esistente
    # per qualunque label sconosciuta, non specifico di questo adapter).
    _LABEL_MAP = {
        "text": "Text",
        "title": "Title",
        "doc_title": "Title",
        "table": "Table",
        "equation": "Formula",
        "image": "Picture",
        "chart": "Picture",
    }

    # Verificato su mineru_client.py (agosto 2026): stesso pattern del client
    # ufficiale. Coordinate in scala 0-1000 (v. docstring della classe).
    _LAYOUT_RE = re.compile(
        r"<\|box_start\|>(\d+)\s+(\d+)\s+(\d+)\s+(\d+)"
        r"<\|box_end\|><\|ref_start\|>(\w+?)<\|ref_end\|>"
        r"(?:<\|rotate_(?:up|right|down|left)\|>)?"
        r"(.*?)(?=<\|box_start\|>|$)",
        re.DOTALL,
    )

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        if task in self._PROMPTS:
            return self._PROMPTS[task]
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': task '{task}' non supportato dal protocollo verificato"
        )

    def parse_layout(self, raw: str) -> list[dict]:
        items = []
        for m in self._LAYOUT_RE.finditer(raw or ""):
            x1, y1, x2, y2, category, tail = m.groups()
            x1i, y1i, x2i, y2i = int(x1), int(y1), int(x2), int(y2)
            # Come il client ufficiale: bbox fuori scala o degeneri scartati,
            # non troncati — un bbox inventato è peggio di uno mancante.
            if not (0 <= x1i < x2i <= 1000 and 0 <= y1i < y2i <= 1000):
                continue
            label = self._LABEL_MAP.get(category.lower(), category)
            items.append({
                "bbox": [x1i, y1i, x2i, y2i],
                "label": label,
                "content": (tail or "").strip(),
            })
        if items:
            return items
        # Verificato live (non documentato dalla guida ufficiale): su una
        # pagina dominata da un'unica tabella a piena pagina — il caso
        # normale per il corpus Historic Shipping Index — "Layout Detection:"
        # a volte non emette alcun wrapper `<|box_start|>`/`<|ref_start|>` e
        # restituisce direttamente il dump OTSL dell'intera pagina. Trattarlo
        # come rumore (0 blocchi) perderebbe una pagina intera di dati; lo si
        # tratta invece come un solo blocco Table a piena pagina, e il
        # riconoscimento vero arriva dal passaggio a bande già esistente
        # (`table_grid`), più accurato di un unico OTSL non bandizzato su
        # scansioni ad alta risoluzione (stesso motivo per cui MonkeyOCRv2 lo
        # fa già, v. `VllmClient.table_grid`).
        if "<fcel>" in raw or "<ecel>" in raw or "<nl>" in raw:
            return [{"bbox": [0, 0, 1000, 1000], "label": "Table", "content": ""}]
        return items

    def sampling_for(self, task: str) -> dict:
        # Verificato live contro l'endpoint reale (non solo sul sorgente): la
        # prima versione mandava solo temperature/top_p/top_k e produceva un
        # loop di ripetizioni — mancavano `vllm_xargs.no_repeat_ngram_size`
        # (il parametro che il `--logits-processors` custom legge davvero:
        # `presence_penalty`/`frequency_penalty` da soli non bastano) e
        # `skip_special_tokens: false` (senza, vLLM ripulisce
        # `<|box_start|>`/`<|ref_start|>` dall'output prima di restituirlo,
        # e `parse_layout` non trova più nulla da parsare — riprodotto:
        # output di solo testo, zero blocchi). Valori di default verificati
        # su `MinerUSamplingParams` in mineru_client.py (agosto 2026).
        base: dict = {
            "top_p": 0.01,
            "top_k": 1,
            "skip_special_tokens": False,
            "vllm_xargs": {"no_repeat_ngram_size": 100},
        }
        if task == "layout":
            return base
        if task == "table":
            # Verificato live: max_tokens=8192 (default di `table_grid`) da
            # solo, su un modello con contesto TOTALE (input+output) di 8192,
            # riempie l'intero budget lasciando 0 token per immagine+prompt —
            # 400 "0 characters, which is the upper bound for 0 input
            # tokens" riprodotto. MonkeyOCRv2 (contesto 24576) non ha questo
            # problema con lo stesso default; qui va abbassato per lasciare
            # spazio all'immagine della banda. frequency_penalty più basso
            # per le tabelle: celle ripetute (stessa colonna, valori uguali)
            # sono legittime, non un loop.
            return {**base, "presence_penalty": 1.0, "frequency_penalty": 0.005, "max_tokens": 2048}
        return {**base, "presence_penalty": 1.0, "frequency_penalty": 0.05}

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        # `--dtype`/`--gpu-memory-utilization`/`--max-model-len`/
        # `--max-num-seqs`/`--max-num-batched-tokens` non sono raccomandati da
        # nessuna guida ufficiale OpenDataLab (nessuna VRAM minima dichiarata,
        # solo benchmark su A100): valori scelti qui per una GPU consumer da
        # 8 GB, non vendor-verificati. Nota: il `config.json` del checkpoint
        # dichiara `max_position_embeddings=32768` a livello top-level ma
        # `8192` nella sotto-struct `text_config` — discrepanza non spiegata
        # da OpenDataLab; 16384 è una scelta intermedia prudente.
        return [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--logits-processors", "mineru_vl_utils:MinerULogitsProcessor",
            "--dtype", "bfloat16",
            "--gpu-memory-utilization", "0.75",
            "--max-model-len", "16384",
            "--max-num-seqs", "4",
            "--max-num-batched-tokens", "8192",
            "--served-model-name", self.capabilities.served_model_name,
        ]


class UnlimitedOcrAdapter(_StubAdapter):
    """baidu/Unlimited-OCR — discendente di DeepSeek-OCR (SAM-ViT-B + CLIP-L
    DeepEncoder, modalità *gundam* per pagina singola), 3B parametri, MIT.
    Parsing one-shot documento → markdown con token di grounding
    ``<|ref|>tipo<|/ref|>`` / ``<|det|>tipo [bbox]<|/det|>`` per riga. Le
    **tabelle** arrivano però in HTML dentro il blocco ``<|det|>table …``, non
    in markdown: verificato sull'immagine ufficiale su pagina `A` del campione. È il
    formato che `prefill` già converte con ``otsl.html_to_otsl``.

    Fatti verificati sulla card Hugging Face e sulla ricetta vLLM ufficiale
    (recipes.vllm.ai/baidu/Unlimited-OCR, agosto 2026):
      - il serving richiede l'**immagine Docker dedicata**
        ``vllm/vllm-openai:unlimited-ocr``: l'architettura NON è ancora nella
        wheel stabile di vLLM, quindi ``pip install vllm`` non basta. Per il
        locale, `serve_command` lancia direttamente questa immagine con
        `docker run --gpus all` (richiede Docker + nvidia-container-toolkit
        sulla macchina) montando i pesi già scaricati dal registro modelli
        invece di farli riscaricare dentro il container; per il cloud resta
        la template Modal ``modal_unlimited_ocr.py`` (usa
        `modal.Image.from_registry` sulla stessa immagine). `stop()` di
        `serve_manager` invia SIGTERM al processo `docker run`: Docker lo
        inoltra al container per lo stop, ma non è istantaneo come un
        processo nativo — normale, non un bug.
        La card HF dichiara "una singola GPU >= 8GB è sufficiente per
        l'inferenza BF16": margine stretto ma dichiarato fattibile su una
        RTX 4060 Laptop;
      - sul server è **obbligatorio** il logits processor
        ``vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor``
        (senza, i documenti lunghi vanno in loop sui token ``<|det|>``), con
        ``--no-enable-prefix-caching`` e ``--mm-processor-cache-gb 0``;
      - per richiesta: il prompt deve iniziare con il prefisso letterale
        ``<image>``, ``skip_special_tokens=False`` e
        ``vllm_xargs {ngram_size: 35, window_size: 128}`` (1024 per
        multi-pagina). Tabularium invia questi parametri e converte i marker
        ``<|det|>`` in blocchi con bbox 0–1000 e contenuto editabile;
      - training: supporto ufficiale **ms-swift** annunciato il 2026/07/21 —
        la stessa toolchain di MonkeyOCRv2 — ma la ricetta di serializzazione
        non è ancora verificata sul repo e il template di training non è
        integrato: `training_types` dichiara l'intenzione, non un percorso
        pronto.

    Misurato su LSI_17186_015 (2864x3952) con l'immagine ufficiale su una
    RTX 4060 Laptop 8 GB, pesi in offload (`--cpu-offload-gb 4`, 2.23 GiB
    residenti in VRAM):
      - end2end pagina intera: 5 blocchi, ~264 s. Le coordinate coincidono con
        quelle di MonkeyOCRv2 a 1 MP, ma il masthead viene separato in due
        `title` e — soprattutto — il registro è etichettato **`table`**, cosa
        che MonkeyOCRv2 su questo corpus non fa mai (lo classifica `Text`, e la
        promozione a tabella la fa `prefill._looks_like_table` su base
        geometrica);
      - throughput 13.3 tok/s contro 152.6 di MonkeyOCRv2 residente e 16.7 di
        DeepSeek-OCR-2 pure in offload: il costo è la banda PCIe, non il
        modello.
    """

    adapter_id = "unlimited-ocr"
    # Unlimited-OCR termina con markdown e marker di grounding, non con una
    # lista JSON: il client deve attendere il finish del server.
    end2end_output_format = "grounded-markdown"
    # La recipe vLLM ufficiale costruisce il messaggio come testo poi immagine;
    # il prompt contiene il token letterale <image> prima del contenuto.
    multimodal_content_order = "text-image"
    end2end_max_tokens = 8192
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="Unlimited-OCR",
        tasks=("layout", "text", "table"),
        coordinate_system="normalized-0-1000",
        # Il modello emette `<table>...</table>` per le tabelle, non markdown:
        # misurato, non dedotto dalla card.
        table_format="html",
        training_types=("lora", "full"),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("multilingual",),
        hf_repo="baidu/Unlimited-OCR",
        approx_size_gb=6.0,
        license_note="MIT.",
        train_toolchain="ms-swift",
        serve_backend="docker-vllm-openai",
        serve_image="vllm/vllm-openai:unlimited-ocr",
        # Serving e inferenza end2end verificati sul campo (v. sotto), training
        # e export no: `experimental`, non `supported`.
        maturity="experimental",
        # La ricetta ufficiale non impone `--max-model-len`, quindi varrebbe il
        # default del checkpoint (32768, da `config.json`). Su una GPU singola
        # da 8 GB non ci sta nemmeno spostando i pesi in RAM: misurato, per
        # 32768 token servono 1.88 GiB di cache KV e ne restano 0.93 dopo
        # l'offload. Il tetto è tarato su quella misura (~0.057 GiB ogni 1000
        # token) e resta ben sopra il budget dell'adapter stesso
        # (`end2end_max_tokens` 8192 + ~1300 token di immagine + prompt).
        # Stessa natura dei flag empirici già documentati per MonkeyOCRv2 in
        # docs/LOCAL_INFERENCE_GUIDE.md: taratura di macchina, non requisito
        # del produttore — su una GPU capiente si può alzare.
        max_model_len=12288,
        served_model_name="Unlimited-OCR",
    )

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        if task == "end2end":
            return "<image>document parsing."
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': solo il percorso end2end è supportato"
        )

    def request_overrides(self, task: str) -> dict:
        if task != "end2end":
            return {}
        return {
            "skip_special_tokens": False,
            "vllm_xargs": {"ngram_size": 35, "window_size": 128},
        }

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return [
            "docker", "run", "--rm", "--gpus", "all",
            "--network", "host", "--ipc", "host",
            "-v", f"{model_path}:/model",
            "vllm/vllm-openai:unlimited-ocr",
            "/model",
            "--trust-remote-code",
            "--logits_processors", "vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
            "--max-model-len", str(self.capabilities.max_model_len),
            "--port", str(port),
            "--gpu-memory-utilization", "0.85",
            "--served-model-name", self.capabilities.served_model_name,
        ]

    def parse_layout(self, raw: str) -> list[dict]:
        marker = re.compile(
            r"<\|det\|>\s*([^\[<\s]+)\s*\[([^\]]+)\]\s*<\|/det\|>(.*?)(?=<\|det\|>|\Z)",
            re.DOTALL,
        )
        items: list[dict] = []
        for match in marker.finditer(raw or ""):
            values = re.findall(r"-?\d+(?:\.\d+)?", match.group(2))
            if len(values) != 4:
                continue
            bbox = [float(value) for value in values]
            if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                continue
            content = re.sub(
                r"<\|ref\|>.*?<\|/ref\|>", "", match.group(3), flags=re.DOTALL
            ).strip()
            items.append({"bbox": bbox, "label": match.group(1), "content": content})
        return items


class DotsOcrAdapter(_StubAdapter):
    """dots-studio/dots.mocr — VLM singolo per layout+contenuto in una sola
    generazione, tabelle in HTML (convertibili con `otsl.html_to_otsl()`).
    Il publisher ha trasferito il repository da ``rednote-hilab`` a
    ``dots-studio``: il registro punta al repo corrente, non al redirect
    storico, così il download e la visualizzazione del modello sono coerenti.

    `serve_command` e il prompt "end2end" sono verificati sul README
    ufficiale. Lo schema esatto delle chiavi JSON restituite (`category` vs
    `label`, forma dell'oggetto) NON è verificato contro un server reale:
    `_tolerant_items()` in `inference.py` cerca la chiave `label`, che il
    README descrive come `category` — va controllato prima di fidarsi del
    parsing, non solo del prompt.

    `approx_size_gb` corretto a 6.1 (verificato dai due shard safetensors
    reali su Hugging Face, agosto 2026): la stima precedente di 3.4 GB era
    quella dichiarata per il solo componente LLM (1.7B), non il checkpoint
    intero servito da vLLM (~3B totali). `--chat-template-content-format
    string` è **obbligatorio** secondo il README ufficiale e mancava dal
    comando qui sotto — non risulta in conflitto con l'invio di contenuto
    multimodale in stile OpenAI (image_url + text) fatto da `VllmClient._chat`:
    quel flag controlla solo come vLLM serializza il testo per il chat
    template, l'estrazione dei dati immagine avviene prima e non ne dipende.
    `--gpu-memory-utilization`/`--max-model-len` non sono nel README (nessuna
    raccomandazione ufficiale per una GPU consumer): valori scelti qui per
    lasciare margine su una scheda da 8 GB dato il checkpoint reale da 6+ GB,
    non valori vendor-verificati."""

    adapter_id = "dots-ocr"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="dots.mocr",
        tasks=("layout", "text", "table"),
        coordinate_system="unverified",
        table_format="html",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "multilingual-partial"),
        hf_repo="dots-studio/dots.mocr",
        approx_size_gb=6.1,
        license_note='Richiede la dicitura "Built with dots.ocr" su pesi/derivati ridistribuiti.',
        train_toolchain="community",
        serve_backend="vllm-openai",
        served_model_name="dots-mocr",
    )

    _PROMPTS = {
        "end2end": (
            "Please output the layout information from the PDF image, including each "
            "layout element's bbox, its category, and the corresponding text content."
        ),
    }

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        if task in self._PROMPTS:
            return self._PROMPTS[task]
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': prompt per '{task}' non verificato — "
            "solo il percorso end2end è confermato sul README ufficiale"
        )

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--tensor-parallel-size", "1",
            "--gpu-memory-utilization", "0.80",
            "--max-model-len", "16384",
            "--chat-template-content-format", "string",
            "--served-model-name", self.capabilities.served_model_name,
            "--trust-remote-code",
        ]


class GlmOcrAdapter(_StubAdapter):
    """zai-org/GLM-OCR — percorso di fine-tuning più documentato del gruppo
    (tutorial ufficiale LLaMA-Factory, full-FT e LoRA).

    `serve_command` verificato sul README ufficiale GitHub zai-org/GLM-OCR e
    sulla recipe vLLM (docs.vllm.ai/projects/recipes, agosto 2026): comando
    `vllm serve` con `--speculative-config` per l'MTP draft ufficiale
    (accelera la decodifica, non cambia l'output). Richiede vLLM >= 0.19.0.
    Architettura nativa (`model_type: glm_ocr`, Transformers >= 5.0.0):
    `--trust-remote-code` non è nel comando ufficiale — se `vllm serve`
    rifiuta l'architettura su una wheel meno recente, aggiungerlo è il primo
    tentativo. `approx_size_gb` misurato dal singolo file
    `model.safetensors` reale su Hugging Face (non dichiarato dalla card).
    `--max-num-batched-tokens 32768` evita l'errore noto "exceeds
    pre-allocated encoder cache size" su immagini ad alta risoluzione
    (segnalazione community, non nel README). `--gpu-memory-utilization`/
    `--max-model-len` non sono raccomandati ufficialmente per hardware
    specifico ("secondo la propria macchina"): valori scelti qui per un
    checkpoint molto piccolo (~2.7 GB) su una GPU da 8 GB."""

    adapter_id = "glm-ocr"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="GLM-OCR",
        tasks=("layout", "text", "table"),
        coordinate_system="unverified",
        table_format="markdown",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh", "multilingual-partial"),
        hf_repo="zai-org/GLM-OCR",
        approx_size_gb=2.7,
        license_note="Codice Apache-2.0, pesi MIT.",
        train_toolchain="llama-factory",
        serve_backend="vllm-openai",
        served_model_name="glm-ocr",
    )

    _PROMPTS = {
        "layout": "Return the document layout as a JSON array in reading order. Each item must have bbox [x1,y1,x2,y2] normalized from 0 to 1000 and category.",
        "text": "Transcribe all text in this image exactly. Return plain text only.",
        "table": "Extract the table in this image in OTSL format. Preserve every row, column, empty cell, and merged-cell span.",
    }

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        prompt = self._PROMPTS.get(task)
        if prompt is None:
            raise NotImplementedError(f"adapter '{self.adapter_id}': task '{task}' non supportato")
        return prompt

    def serialize_target(self, task: str, value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if task == "layout" else str(value)

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--speculative-config", '{"method": "mtp", "num_speculative_tokens": 3}',
            "--max-num-batched-tokens", "32768",
            "--max-model-len", "16384",
            "--gpu-memory-utilization", "0.85",
            "--served-model-name", self.capabilities.served_model_name,
        ]


class DeepSeekOcrAdapter(_StubAdapter):
    """deepseek-ai/DeepSeek-OCR-2 — variante corrente della famiglia DeepSeek
    OCR, con compressione ottica del contesto utile per pagine molto dense
    (\"Gundam mode\"). Parser e prompt restano da verificare nel protocollo
    Tabularium: l'adapter resta uno stub per l'integrazione OCR, ma è ora
    servibile (locale/cloud).

    `serve_command` verificato su recipes.vllm.ai/deepseek-ai/DeepSeek-OCR-2
    (agosto 2026): richiede **vLLM >= 0.12.0** (l'architettura ha una storia
    di breaking change fra versioni vLLM — verificare con `vllm serve --help`
    prima del deploy) e `--trust-remote-code`. Il logits processor
    dedicato evita loop di generazione su documenti lunghi, sullo stesso
    principio di MinerU2.5/Unlimited-OCR. La "Gundam mode" (crop tiling) è
    hardcoded nell'integrazione vLLM, nessun flag la controlla. Licenza
    corretta ad Apache-2.0 (la card HF non è MIT come indicato in precedenza).
    `--max-model-len`/`--gpu-memory-utilization` non ufficiali per hardware
    specifico: la card dichiara "una singola GPU >= 8GB è tipicamente
    sufficiente per l'inferenza BF16", quindi margine stretto su una 8GB."""

    adapter_id = "deepseek-ocr"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="DeepSeek-OCR-2",
        tasks=("layout", "text", "table"),
        coordinate_system="unverified",
        table_format="markdown",
        training_types=(),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh", "multilingual-partial"),
        hf_repo="deepseek-ai/DeepSeek-OCR-2",
        approx_size_gb=6.0,
        license_note="Apache-2.0.",
        train_toolchain="unsloth",
        serve_backend="vllm-openai",
        served_model_name="deepseek-ocr-2",
        max_model_len=8192,
    )

    _PROMPTS = {
        "layout": "Return the document layout as a JSON array in reading order. Each item must contain bbox [x1,y1,x2,y2] normalized from 0 to 1000 and label.",
        "text": "<image>\nFree OCR. Transcribe the image exactly and return only the recognized text.",
        "table": "<image>\nExtract this table in OTSL format. Preserve every row, column, empty cell, and merged cell.",
    }

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        prompt = self._PROMPTS.get(task)
        if prompt is None:
            raise NotImplementedError(f"adapter '{self.adapter_id}': task '{task}' non supportato")
        return prompt

    def serialize_target(self, task: str, value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if task == "layout" else str(value)

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--trust-remote-code",
            "--logits-processors", "vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
            "--max-model-len", "8192",
            "--gpu-memory-utilization", "0.85",
            "--served-model-name", self.capabilities.served_model_name,
        ]


class PaddleOcrVlAdapter(_StubAdapter):
    """PaddlePaddle/PaddleOCR-VL-1.6 — punteggio più alto su OmniDocBench fra
    i candidati studiati, 0.9B, ma il fine-tuning ufficiale passa da ERNIEKit
    (toolchain diversa da ms-swift/LLaMA-Factory). Tabularium genera il dataset
    ERNIEKit e la recipe PaddleX; l'avvio resta esplicito dopo il preflight.

    `serve_command` e i prompt di riconoscimento sono verificati sulla guida
    ufficiale vLLM (docs.vllm.ai/projects/recipes/en/stable/PaddlePaddle/
    PaddleOCR-VL.html, agosto 2026). Nel VLM standalone non esiste un task di
    layout/detection via prompt: riconosce il contenuto di un crop. La pipeline
    PaddleOCR-VL completa aggiunge PP-DocLayoutV3/PP-DocLayoutV2 e produce
    layout, Markdown e tabelle HTML con celle unite. Nel prefill Tabularium
    usiamo il pipeline ufficiale PaddleOCRVL con layout detection attivo e il
    VLM per il riconoscimento dei blocchi; non inventiamo un prompt layout.
    `table_format="html"` è coerente con l'output strutturato documentato.

    `--max-model-len`/`--gpu-memory-utilization` aggiunti dopo verifica: il
    `config.json` dichiara `max_position_embeddings=131072`, e senza un tetto
    esplicito vLLM lo usa come default, precallocando una KV cache enorme
    (issue vLLM #31372, "Using max model len 131072") — sproporzionato per un
    uso OCR su crop. Nessuna delle due soglie è raccomandata ufficialmente per
    una GPU consumer (i maintainer confermano solo una RTX 3060 12GB come
    configurazione minima testata): i valori qui sono una stima prudente per
    una 8GB, da validare empiricamente."""

    adapter_id = "paddleocr-vl"
    # Il pipeline ufficiale contiene già il layout detector PaddleX; il
    # fallback OCR locale appartiene solo alla modalità ocr separata.
    page_layout_fallback = "official-pipeline"
    # I registri lunghi (es. LSI_17186_015) vanno riconosciuti come un unico
    # crop tabellare. Il passaggio generico "OCR:" ha un budget più piccolo e
    # tronca il contenuto prima del fondo pagina.
    table_recognition_strategy = "full_crop"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="PaddleOCR-VL-1.6",
        tasks=("layout", "text", "table", "formula"),
        coordinate_system="unverified",
        table_format="html",
        # Dataset e recipe sono integrati; l'esecuzione richiede checkout e
        # config ufficiali forniti dall'utente.
        training_types=("full",),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh") + tuple(f"lang-{i}" for i in range(1)),  # 100+ lingue dichiarate, non enumerate qui
        hf_repo="PaddlePaddle/PaddleOCR-VL-1.6",
        approx_size_gb=1.8,
        license_note="",
        train_toolchain="erniekit+paddlex",
        serve_backend="vllm-openai",
        served_model_name="PaddleOCR-VL-1.6",
        max_model_len=12288,
    )

    _PROMPTS = {
        "text": "OCR:",
        "table": "Table Recognition:",
        "formula": "Formula Recognition:",
    }

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        if task in self._PROMPTS:
            return self._PROMPTS[task]
        raise NotImplementedError(
            f"adapter '{self.adapter_id}': task '{task}' non supportato — "
            "il modello fa solo recognition (OCR/Table/Formula), non layout/detection"
        )

    def sampling_for(self, task: str) -> dict:
        if task == "table":
            # Lascia spazio sufficiente per il registro completo; il modello
            # ha max-model-len=12288 e vLLM riduce comunque il budget effettivo
            # se il prompt multimodale occupa parte del contesto.
            return {"max_tokens": 4096}
        return {}

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--trust-remote-code",
            "--max-num-batched-tokens", "16384",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
            "--max-model-len", "12288",
            "--gpu-memory-utilization", "0.85",
            "--served-model-name", self.capabilities.served_model_name,
        ]


class Qwen3VlAdapter(_StubAdapter):
    """Qwen/Qwen3-VL-8B-Instruct — VLM generalista, non specializzato
    documenti, ma con l'ecosistema di fine-tuning più maturo in assoluto
    (LoRA su singola GPU, compatibile con i template stock di ms-swift: stessa
    toolchain già in uso per MonkeyOCRv2, senza bisogno di un nuovo env).
    Il repo corrente è ``Qwen/Qwen3-VL-8B-Instruct``; la model card lo
    dichiara come checkpoint BF16 da circa 9B parametri e documenta serving
    vLLM. Il parser Tabularium dedicato resta però da implementare: non va
    promosso automaticamente a prefill strutturato.

    `serve_command` verificato: architettura nativa in vLLM >= 0.11.0
    (nessun `--trust-remote-code` richiesto). `approx_size_gb` corretto a
    16.3 (peso reale bf16 misurato dai file su Hugging Face, non i 9 GB dei
    "parametri" — la confusione tipica fra conteggio parametri e dimensione
    su disco in bf16, 2 byte/parametro). Su questo checkpoint il peso da solo
    supera una GPU consumer da 8 GB: esiste una variante ufficiale FP8
    (``Qwen/Qwen3-VL-8B-Instruct-FP8``, ~9.9 GB) e alcune AWQ 4-bit
    community (~7 GB, es. ``cyankiwi/Qwen3-VL-8B-Instruct-AWQ-4bit``), ma
    nessuna lascia margine reale per KV cache + vision encoder su 8 GB —
    resta comunque provabile (nessun blocco), il warning di dimensione lo
    segnala. `--max-model-len 32768` è un default pratico per documenti, non
    il tetto nativo (256K, estendibile a 1M con YaRN)."""

    adapter_id = "qwen3-vl-8b"
    capabilities = ModelCapabilities(
        adapter_id=adapter_id,
        display_name="Qwen3-VL-8B",
        tasks=("layout", "text", "table", "formula"),
        coordinate_system="unverified",
        table_format=None,
        training_types=("lora", "full"),
        inference_modes=("vllm",),
        hardware=("cuda",),
        languages=("en", "zh", "multilingual-partial"),
        hf_repo="Qwen/Qwen3-VL-8B-Instruct",
        approx_size_gb=16.3,
        license_note="Apache-2.0.",
        train_toolchain="ms-swift",
        serve_backend="vllm-openai",
        served_model_name="qwen3-vl-8b",
        max_model_len=32768,
    )

    _PROMPTS = {
        "layout": "Identify all document elements in reading order. Return only a JSON array; each item must contain bbox [x1,y1,x2,y2] normalized to 0-1000 and label.",
        "text": "Transcribe the text in the image exactly. Return plain text only.",
        "table": "Extract the table exactly in OTSL format. Preserve all rows, columns, empty cells, and merged cells.",
        "formula": "Transcribe the formula as LaTeX.",
    }

    def prompt_for(self, task: str, label: str | None = None) -> str | None:
        prompt = self._PROMPTS.get(task)
        if prompt is None:
            raise NotImplementedError(f"adapter '{self.adapter_id}': task '{task}' non supportato")
        return prompt

    def serialize_target(self, task: str, value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if task == "layout" else str(value)

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        return [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--dtype", "bfloat16",
            "--max-model-len", "32768",
            "--gpu-memory-utilization", "0.90",
            "--limit-mm-per-prompt", '{"image":4,"video":0}',
            "--served-model-name", self.capabilities.served_model_name,
        ]


class CustomVllmAdapter(_StubAdapter):
    """Un modello aggiunto liberamente dall'utente (repo Hugging Face
    qualsiasi), riga della tabella `custom_models` — stesso principio di LM
    Studio: nessun protocollo OCR verificato (prompt/parsing restano
    `NotImplementedError`, ereditati da `_StubAdapter`), solo download e
    serve generico via `vllm serve`. Non è un singleton in `_ADAPTERS`: viene
    costruito al volo da `get_adapter`/`list_adapters` leggendo la riga DB,
    così il resto del registro modelli (download, serve_manager, profili
    compute) lo tratta come un adapter qualunque senza saperlo."""

    def __init__(self, row: dict) -> None:
        self.adapter_id = row["id"]
        self._row = row
        self.capabilities = ModelCapabilities(
            adapter_id=row["id"],
            display_name=row["display_name"],
            tasks=(),
            coordinate_system="unverified",
            table_format=None,
            training_types=(),
            inference_modes=("vllm",),
            hardware=("cuda",),
            hf_repo=row["hf_repo"],
            hf_revision=row.get("hf_revision"),
            approx_size_gb=None,
            license_note="Verificare la licenza sulla scheda Hugging Face del repo.",
            train_toolchain="none",
            serve_backend="vllm-openai",
            served_model_name=row["served_model_name"],
        )

    def serve_command(self, model_path: str, port: int) -> list[str] | None:
        argv = [
            "vllm", "serve", model_path,
            "--port", str(port),
            "--served-model-name", self.capabilities.served_model_name,
            "--gpu-memory-utilization", str(self._row.get("gpu_memory_utilization") or 0.9),
        ]
        if self._row.get("max_model_len"):
            argv += ["--max-model-len", str(self._row["max_model_len"])]
        if self._row.get("trust_remote_code"):
            argv.append("--trust-remote-code")
        extra = (self._row.get("extra_args") or "").strip()
        if extra:
            argv += shlex.split(extra)
        return argv


_ADAPTERS: dict[str, ModelAdapter] = {
    adapter.adapter_id: adapter
    for adapter in (
        MonkeyOCRv2ParsingAdapter(),
        MinerU2_5Adapter(),
        UnlimitedOcrAdapter(),
        DotsOcrAdapter(),
        GlmOcrAdapter(),
        DeepSeekOcrAdapter(),
        PaddleOcrVlAdapter(),
        Qwen3VlAdapter(),
    )
}


def list_adapters() -> list[dict]:
    from . import custom_models  # import locale: evita un ciclo a livello modulo

    items = [asdict(adapter.capabilities) for adapter in _ADAPTERS.values()]
    items += [asdict(CustomVllmAdapter(row).capabilities) for row in custom_models.list_all()]
    return items


def get_adapter(adapter_id: str) -> ModelAdapter:
    if adapter_id in _ADAPTERS:
        return _ADAPTERS[adapter_id]
    from . import custom_models  # import locale: evita un ciclo a livello modulo

    row = custom_models.get(adapter_id)
    if row is not None:
        return CustomVllmAdapter(row)
    raise ValueError(f"adapter modello sconosciuto: {adapter_id}")


def supported_prefill_modes(adapter: ModelAdapter) -> dict[str, bool]:
    """Quali modalità di prefill "a due stadi"/"end2end" l'adapter sa fare
    DAVVERO, sondando `prompt_for` invece di fidarsi di
    `capabilities.tasks` — quel campo descrive il modello in astratto e non
    è affidabile per questo: MonkeyOCRv2 sa fare end2end ma "end2end" non è
    nella sua tupla `tasks`; dots.ocr ha "layout" in `tasks` ma il suo
    `prompt_for` lo rifiuta (solo end2end è verificato). Un'interfaccia che
    mostrasse "due stadi"/"end2end" da `tasks` per questi due casi
    mostrerebbe un pulsante che fallisce sempre — riprodotto: MinerU2.5
    lasciava visibile "end2end" pur non implementandolo."""
    modes = {}
    for mode, task in (
        ("supports_two_stage", "layout"),
        ("supports_end2end", "end2end"),
    ):
        try:
            adapter.prompt_for(task)
            modes[mode] = True
        except NotImplementedError:
            modes[mode] = False
    # Il percorso nativo è ciò che il prefill offre oggi: basta che l'adapter
    # abbia un prompt verificato per una sola via (il resolver `_native_mode`
    # sceglie end2end quando c'è, altrimenti il protocollo a due passi).
    modes["supports_native"] = (
        modes["supports_two_stage"]
        or modes["supports_end2end"]
        or getattr(adapter, "page_layout_fallback", None) in {"ocr", "official-pipeline"}
    )
    return modes


def supports_export(adapter: ModelAdapter) -> bool:
    """L'adapter può ricevere l'export dataset (prompt per layout/testo/tabella
    tutti verificati)? Lo stesso principio di `supported_prefill_modes`: il
    builder chiama `prompt_for` per ciascuna famiglia (v. `dataset_builder`),
    quindi un adapter di sola ricetta (`_StubAdapter`) — e chi non ha un
    prompt per anche una sola famiglia, es. dots.ocr e PaddleOCR-VL —
    produrrebbe un export che finisce sempre in `NotImplementedError`. La UI
    deve filtrare il selettore dell'export, non offrire opzioni morte."""
    for task in ("layout", "text", "table"):
        try:
            adapter.prompt_for(task)
        except NotImplementedError:
            return False
    return True
