# Contributing to Tabularium

Thanks for your interest. Bug reports, documentation fixes, model adapters and
test pages from new kinds of archives are all welcome.

## Before you start

- For anything larger than a small fix, open an issue first so we can agree on
  the approach.
- [AGENTS.md](AGENTS.md) is the product spec and records why things are the way
  they are. Read the section for the area you touch.

## Setup

Follow the [installation guide](docs/guide/installation.md), then run the checks
described in [docs/guide/development.md](docs/guide/development.md):

```bash
cd backend && python -m pytest tests -q
cd frontend && npm run typecheck && npm test && npm run build
```

## Conventions

- Everything in English: code, comments, docs, commit messages. Existing Italian comments
  are translated when you touch the surrounding code.
- No hard-coded UI strings: every text goes through the i18n dictionaries
  (`frontend/src/i18n/{it,en,fr}.ts`), with the same placeholders in all three.
- Every new API route is protected by the permission layer.
- Never destroy user data implicitly: destructive actions ask for confirmation.
- Model-specific conventions (prompts, coordinates, OTSL, dataset formats) must
  match the model's official repository. Cite it in the PR.
- Update the docs and `CHANGELOG.md` when behaviour changes.

## Adding a model

A model is an adapter (`backend/app/services/model_adapters.py`) plus its native
label map (`model_labels.py`) and, when trainable, a training recipe. A model is
marked as verified only after a real run on real pages: say in the PR what you
measured and on which hardware.

## Sharing pages

Please don't attach scans you don't have the right to share. Public-domain
pages are perfect for bug reports.
