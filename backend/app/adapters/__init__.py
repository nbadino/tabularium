"""The outside world: SQLite, the filesystem, HTTP, subprocesses.

Phase P3 of `docs/architecture/program.md`. A service asks an adapter for data and
receives values; the adapter owns the query, the path or the request. This is where a
change of storage, transport or vendor stops.
"""
