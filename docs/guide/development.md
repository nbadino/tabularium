# Development

## Tests

```bash
# backend tests
cd backend && python -m pytest tests -q

# frontend: typecheck, unit/component tests, build
cd frontend && npm run typecheck && npm test && npm run build

# end-to-end smoke (needs a running instance and Chromium)
cd frontend && npm run test:e2e
```

Continuous integration runs the backend tests (Python 3.11–3.13), the frontend
checks, a macOS pass and the end-to-end workflow (`.github/workflows/ci.yml`).

## Architecture

One backend process serves both the API and the built frontend:

```
backend  (FastAPI + SQLite + Pillow/NumPy; model clients; subprocess for training)
  services/   recognition sessions · table detection · OTSL encoding · dataset builder
              trainer orchestration · VRAM preflight · runtimes · auth/permissions

frontend (React 19 + Vite + TypeScript + Tailwind; Konva canvas; spreadsheet table workspace)
  studio/     zoom/pan canvas · blocks · reading order · table workspace
  app/        auth gate · layout · command palette · i18n (English / Italiano / Français)
```

The full product spec, data conventions and the reasoning behind every design
decision are in [AGENTS.md](https://github.com/nbadino/tabularium/blob/main/AGENTS.md) (Italian; the source of truth).

## Comparing models from the command line

Point the non-destructive benchmark at already-running endpoints (one
`--target` per server); results are never written as labels:

```bash
PYTHONPATH=backend data/vllm-runtime/bin/python scripts/benchmark_models.py \
  --image path/to/page.jpg \
  --target monkeyocrv2-parsing,http://127.0.0.1:8888/v1,MonkeyOCRv2 \
  --target mineru2.5,http://127.0.0.1:8889/v1,mineru2.5 \
  --task layout --repeat 2 --output data/benchmarks/run.json
```

It records success/failure, latency, time to first token, tokens/s, output
validity and token usage. Use `--task end2end` for adapters whose layout
protocol is not verified. For accuracy against your own gold pages, use the
**Benchmark** page in the app.

## Versioning

The application version lives in `VERSION`; the version displayed by the
backend and the frontend package metadata are synchronized by the helper:

```bash
python3 scripts/version.py                 # show current version
python3 scripts/version.py bump patch      # 0.1.0 → 0.1.1
python3 scripts/version.py bump minor      # 0.1.0 → 0.2.0
python3 scripts/version.py bump major      # 0.1.0 → 1.0.0
```

After a bump, add user-facing notes under the new version in
[CHANGELOG.md](https://github.com/nbadino/tabularium/blob/main/CHANGELOG.md),
commit, then push a tag matching `VERSION`:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The `release` workflow (`.github/workflows/release.yml`) builds the frontend,
packs the repository with `frontend/dist` included (plus a `.prebuilt` marker
so `run.sh` never tries to rebuild it), and publishes `.tar.gz`, `.zip` and
`SHA256SUMS.txt` with the changelog section as release notes. It refuses a tag
that doesn't match `VERSION`. Running it manually from the Actions tab builds
the archives as artifacts without publishing anything.
