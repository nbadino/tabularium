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

**A local model server stays "loading" behind a network proxy.** Up to 0.7 Python sent the
app's calls to its own server on `127.0.0.1` through the operating system's proxy (macOS,
Windows) when the proxy had no `localhost` exception. Tabularium now always adds
`127.0.0.1`, `localhost` and `::1` to `no_proxy` and keeps the system proxy for the internet.
If downloads fail too, check the proxy itself: on macOS the server field takes the host only
(`proxy.example.org`), not `http://proxy.example.org`.

**OCR fails to import on a headless Linux box.**
Install `libgl1` and `libglib2.0-0` (see [Installation](installation.md#linux-prerequisites)).
