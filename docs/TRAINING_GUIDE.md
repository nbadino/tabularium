# Guida al training (M18)

Questa guida è il **registro** del training multi-modello di Tabularium: la matrice delle
ricette, la procedura **ripetibile** per verificarle su GPU, e il **log dei test** realmente
eseguiti. La fonte di verità del design resta `AGENTS.md` §M18; qui c'è l'operatività.

## Principi

1. **Una ricetta per adapter** (`services/train_recipes.py`): `toolchain`, doc ufficiale, tipi
   ammessi, vista dati, maturità. Il generatore di script (`trainer_script.py`) dispatcha dalla
   ricetta e **fallisce esplicito** su ciò che non sa eseguire (mai uno script sbagliato in
   silenzio).
2. **Maturità onesta**: `verified` solo dopo una run reale documentata qui sotto; altrimenti
   `experimental`. `planned` = nessuna ricetta eseguibile.
3. **Il toolchain è uno** (ms-swift) per quasi tutti i modelli OCR: la tabella ufficiale
   *Supported-models-and-datasets* di ms-swift fornisce `Model Type | Default Template`.

## Matrice delle ricette

Valori `--model_type`/`--template` dalla tabella ufficiale ms-swift (`main`).

| Adapter | Toolchain | `--model_type` | `--template` | Maturità |
|---|---|---|---|---|
| monkeyocrv2-parsing | ms-swift | `monkeyocrv2` | `monkeyocrv2` | verified |
| qwen3-vl-8b | ms-swift | `qwen3_vl` | `qwen3_vl` | experimental |
| unlimited-ocr | ms-swift | `unlimited_ocr` | `unlimited_ocr` | experimental |
| deepseek-ocr | ms-swift | `deepseek_ocr2` | `deepseek_ocr2` | experimental |
| glm-ocr | ms-swift | `glm_ocr` | `glm_ocr` | experimental |
| mineru2.5 | ms-swift | `qwen2_vl` | `qwen2_vl` | experimental |
| dots-ocr | ms-swift | `dots_ocr` | `dots_ocr` | experimental |
| paddleocr-vl | erniekit+paddlex | — | — | experimental (fuori dal generatore ms-swift) |
| teleocr, lightonocr-3 | — | — | — | planned (non in ms-swift) |
| gemini-argon | — | — | — | non addestrabile (modello chiuso) |

Endpoint: `GET /api/system/train-recipes` (`items`, `trainable`).

## Procedura ripetibile (host GPU)

Preparazione di un host GPU nuovo (Vast.ai o qualsiasi box CUDA) — script
`scripts/cloud/setup_cloud_train.sh`:

```bash
TABULARIUM_TRAIN_ROOT=$HOME/tabularium-train bash scripts/cloud/setup_cloud_train.sh
```

Su Vast.ai il noleggio **prepara da sé** l'ambiente: `rent_vast_instance(..., prepare_training=True,
tabularium_ref=<ref>)` mette nell'`onstart` il curl di `setup_cloud_train.sh` (non quello di
serving) e non apre porte HTTP — al training basta SSH. Lo script scrive
`$TABULARIUM_TRAIN_ROOT/.train_ready` a fine installazione, quindi "preparato" è un fatto su
disco, non un'assunzione.

Dal **wizard di training** (executor «Vast.ai») il pulsante **«Noleggia e prepara»** fa tutto da
sé: `POST /api/system/cloud/vast/rent-training` cerca l'offerta più economica entro i minimi
(`max_dph`, `min_gpu_ram_gb`), la noleggia con `prepare_training` e, quando l'istanza espone
l'SSH, compila host/porta/python/repo. Il ref è **`main`** (ultima versione su GitHub: non ci sono
release).

Percorsi risultanti sull'host (da usare nell'executor di training):

| Cosa | Percorso |
|---|---|
| radice ambiente | `$HOME/tabularium-train` |
| python (venv) | `$HOME/tabularium-train/venv/bin/python` |
| repo di training | `$HOME/tabularium-train/MonkeyOCRv2/parsing/train` |

Poi, dal lato Tabularium, si esporta il dataset e si lancia lo script generato:

```bash
# 1) dataset (dalla pagina Dataset, o via API)
curl -X POST /api/projects/<id>/datasets/build -d '{"split_ratio":0.9}'
# 2) script di training per l'adapter scelto
python -c "from app.services import trainer_script; print(trainer_script.generate_script(cfg, ...))"
# 3) lancio (wizard Fine-tuning → «Questa macchina»/SSH/Vast)
```

Smoke test minimo (verifica che la **ricetta giri**, non la qualità): dataset sintetico di 2
campioni, `--max_steps 1`, `--save_steps 1`. Vedi il log dei test.

## Log dei test

Ogni voce: data, macchina, adapter, comando, esito. Aggiornare a ogni run reale; una ricetta
passa a `verified` **solo** qui.

### 2026-10-18 — generatore e dispatch (senza GPU)
- **Cosa**: `trainer_script.generate_script` produce lo script giusto per `monkeyocrv2-parsing`,
  `qwen3-vl-8b`, `unlimited-ocr`; `paddleocr-vl` (erniekit) e `gemini-argon` (chiuso) falliscono
  esplicito.
- **Esito**: ✅ `tests/test_train_recipes.py`.
- **Limite**: non prova che lo script *giri* su GPU.

### 2026-10-18 — tentativo di smoke test su Vast.ai (bloccato dall'infrastruttura)
- **Cosa**: noleggio di una RTX 3090 per preparare l'ambiente con
  `scripts/cloud/setup_cloud_train.sh` e lanciare un LoRA a 1 step su MonkeyOCRv2.
- **Istanza 56531577** (3090, immagine `pytorch/pytorch:2.4.0-cuda12.1-cudnn9-devel`): bloccata su
  `created` per ~10 min, SSH (proxy e diretto) rifiutato. Distrutta.
- **Istanza 56533272** (3090, immagine Vast di default): `loading` → `created` → `offline`, SSH
  mai accettato. Distrutta.
- **Esito**: ❌ **bloccato lato host Vast.ai**, non da Tabularium (nessuna riga di codice
  coinvolta: l'istanza non è mai arrivata a `running`). Credito speso: ~$0.05.
- **Conseguenza**: nessuna ricetta passa a `verified`; tutte restano `experimental`.
- **Ripetibile**: appena un host Vast (o un'altra GPU) sale a `running`, eseguire
  `TABULARIUM_TRAIN_ROOT=$HOME/tabularium-train bash scripts/cloud/setup_cloud_train.sh`, poi il
  LoRA a 1 step; annotare qui l'esito.


<!-- Prossimo test: smoke test reale su GPU (Vast.ai RTX 3090) -->
