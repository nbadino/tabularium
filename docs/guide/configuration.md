# Configuration

Everything has a working default; these environment variables are optional
overrides.

| Variable | Default | Purpose |
|---|---|---|
| `TABULARIUM_ROOT` | `<repo>/data` | data root (projects, database, crops, runs) |
| `TABULARIUM_HOST` / `TABULARIUM_PORT` | `127.0.0.1` / `8787` | server bind |
| `TABULARIUM_AUTH` | `on` | `off` disables authentication (single-user local mode) |
| `TABULARIUM_REGISTRATION_OPEN` | closed | `1` opens self-registration |
| `TABULARIUM_SESSION_TTL_DAYS` | `30` | session lifetime |
| `TABULARIUM_OCR_ENGINE` | `auto` | `rapidocr` or `paddleocr` |
| `TABULARIUM_VLLM_URL` | `http://127.0.0.1:8888/v1` | inference server endpoint |
| `TABULARIUM_VLLM_MODEL` | `MonkeyOCRv2` | served model name |
| `TABULARIUM_VLLM_MAX_PIXELS` | unset | cap on pixels sent to the layout model |
| `TABULARIUM_TRAIN_REPO` | unset | your own MonkeyOCRv2 checkout (otherwise cloned automatically) |
| `TABULARIUM_TRAIN_ENV` | `monkeyocrv2-train` | conda/venv name for training |
| `TABULARIUM_MODELS_DIR` | `<repo>/models` | local model checkpoints |

## Optional extras

- **Page rectification**: *Align and compare* in the studio proposes a
  corrected page without touching your annotations; you accept or reject it.
  The only neural rectifier is the **official MonkeyOCRv2 preprocessor**, the
  same stage the model's own pipeline runs before parsing; alongside it there
  are rotation-only deskew and manual perspective/mesh corrections.
- **Alternative OCR engines**: see
  [OCR_MODEL_ALTERNATIVES.md](https://github.com/nbadino/tabularium/blob/main/docs/OCR_MODEL_ALTERNATIVES.md).
