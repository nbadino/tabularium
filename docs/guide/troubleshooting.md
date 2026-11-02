# Troubleshooting

**I forgot the administrator password.**
Reset it from the terminal; it uses the same hashing and session invalidation
as the app:

```bash
tabularium reset-password          # list users
tabularium reset-password admin    # interactive (hidden input)
```

`--password` sets it non-interactively, `--data DIR` points at another data folder
(for a service: `sudo -u tabularium env HOME=/var/lib/tabularium
TABULARIUM_ROOT=/var/lib/tabularium/data tabularium reset-password admin`). From a
checkout, `backend/.venv/bin/python scripts/reset_password.py` does the same.

**The app doesn't start / the page is blank.**
The backend serves `frontend/dist`, so the frontend must have been built once
(`scripts/setup_frontend.sh`, or `cd frontend && npm run build`).
`scripts/run.sh`/`run.ps1` rebuild it automatically when its sources are newer
(or with `TABULARIUM_BUILD_FRONTEND=1`). Health is reported on `/api/health`.

**Recognition with a model, evaluation or the playground say the model is unavailable.**
They need a model being served: start one from **Models**. Everything else
(local OCR, annotation, review, export) works without it.

**OCR fails to import on a headless Linux box.**
Install `libgl1` and `libglib2.0-0` (see [Installation](installation.md#linux-prerequisites)).
