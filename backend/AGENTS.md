# backend — local rules

The root [`AGENTS.md`](../AGENTS.md) is the contract; this file adds only what is local.

- **Layers**: `domain/` is pure (stdlib + pure libraries). `adapters/` owns SQLite and, later,
  the filesystem and HTTP. `services/` decides the order. `api/` translates HTTP. The
  direction is enforced by [`tests/test_architecture.py`](tests/test_architecture.py).
- **Before you edit a service that reads the database**: check whether an adapter already
  owns that table (`adapters/store.py`, `projects.py`, `blocks.py`, `runs.py`, `exports.py`).
  If it does, use it; if it does not, add the function there, not a `connect()` here.
- **A model is one file**: `app/models/<id>.py`. See `app/models/base.py` for the four
  required surfaces and `scripts/surfaces.py --check` for the proof.
- **Run** `python -m pytest tests -q` from this directory (or `../scripts/check.sh --fast`).
- **Never** add a migration without a test in `tests/test_db_migrations.py`.
