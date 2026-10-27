"""Pure application logic: no FastAPI, no SQLite, no network, no subprocess.

Phase P3 of `docs/architecture/program.md`. A module belongs here when it can be
reasoned about and tested with values alone — the table format, the VRAM estimate, the
header repair, the similarity metrics. Anything that reads a database, starts a process
or answers an HTTP request lives outside, in `app/adapters` (the outside world) or
`app/services` (orchestration).

`tests/test_architecture.py` enforces the direction: this package may import the
standard library, third-party pure libraries and `app.schemas`, and nothing else from
the application.
"""
