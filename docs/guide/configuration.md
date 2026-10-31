# Configuration

Everything has a working default; these environment variables are optional
overrides. When Tabularium is installed with uv, they can also go in the `.env`
file of the data folder.

| Variable | Default | Purpose |
|---|---|---|
| `TABULARIUM_ROOT` | `~/Tabularium` (installed), `<repo>/data` (from source) | data root (projects, database, crops, runs) |
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
| `TABULARIUM_PUBLIC_URL` | unset | public URL for the OIDC callback when a proxy hides the real address |

## Institutional login (OIDC)

If your institution runs an OpenID Connect provider, the administrator enables it in
**Settings → Identity and access**: issuer, client ID, client secret, scopes, allowed
email domains and the button text. Nothing is configured by environment variables — the
panel is the configuration, and the client secret goes to the encrypted vault and is
never returned by the API.

The redirect URI to register at the provider is:

```
https://<your-host>/api/auth/oidc/callback
```

Set `TABULARIUM_PUBLIC_URL` when the address the app sees is not the one users type
(a proxy that rewrites the host is the usual case). Local accounts keep working: OIDC
adds a second door, it does not replace the first.

Behaviour worth knowing:

- the first login **creates** the account with the instance's default role; an
  administrator who already created that email keeps the existing account and role;
- with **allowed domains** empty, any account the provider authenticates can enter; an
  institution should fill it in;
- an email the provider does not assert as verified (`email_verified` missing or false) is refused, and so is a disabled account;
- the flow uses authorization code + PKCE, and each attempt's `state` is single-use and
  expires after ten minutes.

## Optional extras

- **Page rectification**: *Align and compare* in the studio proposes a
  corrected page without touching your annotations; you accept or reject it.
  The only neural rectifier is the **official MonkeyOCRv2 preprocessor**, the
  same stage the model's own pipeline runs before parsing; alongside it there
  are rotation-only deskew and manual perspective/mesh corrections.
- **Alternative OCR engines**: see
  [OCR_MODEL_ALTERNATIVES.md](https://github.com/nbadino/tabularium/blob/main/docs/OCR_MODEL_ALTERNATIVES.md).
