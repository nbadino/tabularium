# Alternative a MonkeyOCRv2 — cernita modelli OCR/VLM (agosto 2026)

Ricerca esplorativa: modelli open-weight alternativi a `MonkeyOCRv2-B-Parsing` per il
fine-tuning su Lloyd's List (layout multi-colonna denso + registri tabellari con celle
unite). Non è una decisione, è materiale per sceglierne uno o due da testare sul campione
gold esistente prima di eventualmente cambiare stack.

## Criteri di scarto (da AGENTS.md §1–2)

1. **Table recognition con celle unite** (rowspan/colspan) — requisito non negoziabile,
   confermato: i registri Lloyd's sono tabelle dense con celle di una-due cifre.
2. **Fine-tunabile sui nostri dati** — pesi aperti, licenza che permetta di ridistribuire
   un checkpoint derivato, toolchain di training documentata (LoRA e/o full-SFT).
3. **Regge pagine dense** — multi-colonna, testo piccolo, scansioni d'epoca degradate/storte.
4. **Formato tabella compatibile o convertibile in OTSL** — il dataset ms-swift richiede
   OTSL; HTML è accettabile se esiste un convertitore (come `html2otsl.py` già usato).
5. **Dimensione gestibile** su GPU singola (indicativamente ≤8B, idealmente 1–4B, dato che
   MonkeyOCRv2-B è 0.7B totali).

## Tabella comparativa

| Modello | Dim. | Licenza | OmniDocBench v1.5 | Formato tabella | Fine-tuning | Note architettura |
|---|---|---|---|---|---|---|
| **MonkeyOCRv2-B-Parsing** (attuale) | 0.7B | — | 88.57 (pro-3B) | OTSL nativo | ms-swift, LoRA/full-SFT, ViT freeze | due stadi layout+contenuto, END2END alternativo |
| **dots.ocr / dots.mocr** | 1.7B LLM (~3B tot.) | dots.ocr License (MIT-based, richiede attribuzione "Built with dots.ocr") | 90.77 / dots.mocr più alto (media 1124 vs 781 di MonkeyOCR-pro-3B su 3 bench) | HTML | Script community (`wjbmattingly/dots.ocr`, LLaMA-Factory), LoRA testata ma con rough edges | VLM singolo per layout+contenuto in un solo forward, multilingue, converte grafica in SVG |
| **DeepSeek-OCR-2** | 3B | MIT | 90.25 | non nativo OTSL (Markdown-first); tabelle via crop | Notebook Unsloth pronto, riduzioni CER fino a 86% su fine-tuning di dominio con poche decine di esempi | compressione ottica del contesto ("Gundam mode" per pagine dense, +30% fedeltà layout) |
| **GLM-OCR** (zai-org) | non specificato (compatto) | codice Apache-2.0, pesi MIT | 95.22 | Markdown/testo strutturato, robusto a celle unite (dettagli rowspan/colspan non documentati) | Tutorial ufficiale LLaMA-Factory, full-FT e LoRA entrambi documentati | pipeline completa con PP-DocLayoutV3 per il layout |
| **PaddleOCR-VL-1.6 / -1.5** | 0.9B | non verificata in questa ricerca | 96.34 / 94.93 (top benchmark) | valutato su "hard table" set (1258 tabelle, incl. merged-cell) | codice di fine-tuning "in arrivo" — oggi solo via ERNIEKit (toolchain Baidu, meno diffusa) | ultra-compatto, miglior punteggio assoluto ma tooling di FT meno maturo per un team esterno |
| **MinerU2.5 (-Pro)** | 1.2B | non verificata in questa ricerca | 95.75 (Pro) | **OTSL nativo → conversione HTML finale** | data engine dedicato per pretrain/FT, dettagli pubblici limitati | **due stadi come MonkeyOCR**: layout su immagine downscalata + contenuto su crop a risoluzione nativa — architettura più vicina al pipeline già in uso |
| **Qwen3-VL / Qwen2.5-VL (+RolmOCR)** | 2B–235B (dense 2/4/8/32B) | Apache-2.0 | 89.78 (235B, non praticabile in locale) | nessun formato tabella dedicato, va insegnato in fine-tuning | ecosistema di FT più maturo in assoluto (LoRA su singola GPU <32GB, Unsloth, compatibile ms-swift) | VLM generalista, non specializzato documenti: serve più dati di FT per arrivare al livello dei modelli document-native |
| **IBM Granite-Docling-258M** | 0.258B | Apache-2.0 | non nel confronto diretto | **OTSL nativo** (via DocTags, stesso schema IBM già citato in AGENTS.md §2.3) | toolchain Docling/Hugging Face, meno precedenti di fine-tuning per casi complessi | il più leggero in assoluto; rischio di capacità insufficiente su scansioni d'epoca molto degradate — da validare, non da scartare a priori |

Fonti principali: [OmniDocBench (opendatalab)](https://github.com/opendatalab/OmniDocBench),
[dots.ocr (rednote-hilab)](https://github.com/rednote-hilab/dots.ocr),
[DeepSeek-OCR fine-tuning (Unsloth docs)](https://unsloth.ai/docs/models/tutorials/deepseek-ocr-how-to-run-and-fine-tune),
[GLM-OCR fine-tuning (LLaMA-Factory)](https://github.com/zai-org/GLM-OCR/blob/main/examples/finetune/README.md),
[PaddleOCR-VL-1.6 paper](https://arxiv.org/pdf/2606.03264),
[MinerU2.5 paper](https://arxiv.org/html/2509.22186v1),
[Qwen3-VL fine-tuning guide (Datature)](https://datature.io/blog/how-to-fine-tune-qwen3-vl-on-your-own-dataset),
[Granite-Docling-258M (Hugging Face)](https://huggingface.co/ibm-granite/granite-docling-258M).

## Lettura dei risultati

- **OTSL nativo** (requisito §2.5 del progetto) lo hanno solo **MonkeyOCR**, **MinerU2.5**
  e **Granite-Docling**. Tutti gli altri (dots.ocr, GLM-OCR, DeepSeek-OCR, PaddleOCR-VL,
  Qwen-VL) producono HTML o Markdown per le tabelle: riusabili, ma richiedono un
  convertitore verso OTSL per restare nel formato dataset ms-swift, con lo stesso approccio
  già adottato per l'HTML del README ufficiale (§2.3, nota su `html2otsl.py`).
- **MinerU2.5 è l'architettura più vicina a quella già in uso**: due stadi, layout su
  immagine ridotta + contenuto su crop a risoluzione nativa, esattamente lo schema che
  `services/inference.py` implementa oggi con il tetto di 2 MP sulla sola chiamata di
  layout. È il candidato con minor rischio di re-ingegnerizzazione della pipeline attuale.
- **dots.ocr/dots.mocr** ha il margine più ampio sopra MonkeyOCR nei benchmark aggregati
  citati, è un VLM singolo (niente due chiamate separate come MonkeyOCR/MinerU), ma il
  fine-tuning è ancora "in rodaggio" nella community (bug noti, non uno script ufficiale
  Zenos/rednote).
- **GLM-OCR** ha probabilmente il percorso di fine-tuning più maturo e documentato
  (LLaMA-Factory, full-FT e LoRA con tutorial ufficiale passo-passo), il che pesa quanto il
  punteggio benchmark per un team che deve mantenere la pipeline di addestramento.
- **PaddleOCR-VL** vince sui numeri assoluti ma la sua toolchain di fine-tuning (ERNIEKit)
  non è quella con cui il team ha già familiarità (ms-swift); codice di FT dedicato non
  ancora rilasciato al momento di questa ricerca.
- **Qwen3-VL/Qwen2.5-VL** restano il "fallback sicuro": non sono specializzati documenti,
  ma condividono l'ecosistema di training (compatibile ms-swift, LoRA ben rodato) e possono
  assorbire uno stile di annotazione molto vicino a quello già costruito per MonkeyOCR.

## Raccomandazione

Nessuna sostituzione immediata: MonkeyOCRv2 resta la baseline in produzione. Se si vuole
testare un'alternativa, l'ordine di priorità suggerito per una prova sul campione gold
esistente (poche pagine, stesso protocollo di valutazione TEDS/CER/IoU già in uso):

1. **MinerU2.5** — architettura più compatibile con la pipeline esistente, OTSL nativo,
   rischio di integrazione più basso.
2. **dots.ocr / dots.mocr** — margine benchmark più ampio, da validare specificamente sulla
   qualità del fine-tuning (poco documentato) e sulla conversione HTML→OTSL.
3. **GLM-OCR** — se il criterio decisivo è la maturità del percorso di fine-tuning più che
   il punteggio assoluto.

Prima di impegnare tempo di training, vale la pena un giro di **inferenza zero-shot** di
questi tre su 2-3 pagine gold Lloyd's (una con registro tabellare denso, una con layout
multi-colonna misto testo/tabella) per vedere quale regge meglio la degradazione delle
scansioni d'epoca *prima* di scrivere adapter di dataset dedicati.

## Limiti di questa ricerca

Ricerca basata su web search (agosto 2026), non su test diretti sul corpus Lloyd's.
Punteggi benchmark aggregati (OmniDocBench, olmOCR-Bench, XDocParse) misurano documenti
moderni multilingue, non scansioni di giornali storici anni 1900; i numeri vanno letti come
ranking relativo, non come stima diretta delle prestazioni sul dominio Lloyd's. Licenza di
PaddleOCR-VL e dettagli merged-cell di GLM-OCR non sono stati verificati alla fonte primaria.
