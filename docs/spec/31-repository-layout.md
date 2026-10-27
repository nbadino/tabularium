# 5. Repository structure

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

```
tabularium/
  AGENTS.md  README.md  CHANGELOG.md  DESIGN.md  PRODUCT.md  VERSION
  pyproject.toml            # installable package (wheel with app/_bundle, `tabularium` command)
  mkdocs.yml                # documentation site (docs/ → GitHub Pages)
  backend/
    app/
      main.py               # FastAPI entry, serves the built frontend, health
      cli.py                # `tabularium` command
      config.py             # settings, RESOURCE_DIR, data root, .env, vault key
      db.py                 # SQLite schema, versioned migrations
      schemas.py            # Pydantic models
      models/               # one module per model: SPEC = serve + train + labels + cloud
      domain/               # pure logic: otsl, vram, table_headers, table_similarity.
                            #   No FastAPI, SQLite, HTTP or subprocess (tests/test_architecture.py)
      adapters/             # the outside world: store.py (meta key/value), projects.py
                            #   (settings_json), blocks.py (prefill reads/writes), runs.py
                            #   (recognition sessions), exports.py (annotated corpus).
                            #   Services ask for values, never SQL
      api/                  # auth, users, settings, projects, pages, blocks, page_work, prelabel,
                            # recognition, datasets, training, evaluate, benchmark, playground,
                            # models, cloud, system, live (WebSocket), deps (permissions)
      services/             # one module per concern; the main ones:
        model_adapters.py   #   per-model prompts, parsers, capabilities (behaviour)
        model_labels.py     #   native → canonical label bridge
        model_registry.py   #   catalog, downloads, local runnability (cloud templates
                            #   and serve recipes are derived from app/models/)
        serve_recipes.py / serve_manager.py / local_runtime.py / mlx_runtime.py / paddle_runtime.py
        train_recipes.py / trainer.py / trainer_script.py / training_views.py
        training_executor.py
        cloud_manager.py / modal_manager.py   # Vast.ai, RunPod, SSH tunnels, Modal
        inference.py        #   OpenAI-compatible client (+ Gemini generateContent)
        prefill.py / recognition.py / ocr.py / table_detect.py (deprecated, §2.3.5)
        table_checks / dataset_builder / paddle_dataset (still DB-coupled: P3b)
        evaluate.py / benchmark.py
        auth.py / security.py / users.py / vault.py / live.py / page_work.py / i18n.py
    tests/                  # pytest
    requirements.txt        # runtime deps (mirrored in pyproject.toml);
                            # requirements-dev.txt, requirements-uvdoc.txt (PaddleOCR)
  frontend/
    src/
      app/                  # shell: Layout, AuthGate, auth, ui, models hub logic, live, share dialog
      pages/                # one per route (Recognize, Results, Projects, Annotation, Dataset,
                            # Training, Evaluation, Benchmark, Models, Playground, Settings, Users…)
      studio/               # canvas, annotation state + history, components (ContentPane,
                            # UniverSheet, TableWorkspacePanel, PageSidebar, PrefillDialog…)
      lib/                  # api.ts, grid.ts, univerGrid.ts, coords, labels, dates, avatar…
      i18n/                 # it.ts (structure source of truth), en.ts, fr.ts
  conf/profiles/            # domain profiles
  docs/                     # documentation site (docs/guide/*), screenshots, internal notes
  scripts/
    setup_backend.sh/.ps1  setup_frontend.sh/.ps1  run.sh/.ps1  build_frontend.sh/.ps1
    serve_model.sh          # MonkeyOCRv2 serving wrapper
    version.py              # VERSION bump, synced to frontend/package.json
    cloud/                  # scripts sent to remote GPUs (vLLM/training setup, Modal apps…)
    local/                  # local runtime helpers (MinerU MLX gateway)
    e2e_*.py / e2e_smoke.sh # browser end-to-end tests
  .github/workflows/        # ci.yml, docs.yml (GitHub Pages), release.yml (GitHub + PyPI)
  data/                     # (gitignored) local data root when running from source
```

---
