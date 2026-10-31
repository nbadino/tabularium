"""The doctor must answer without the web app, and must not lie about a failure."""
from __future__ import annotations

import json
import socket

from app.services import doctor


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_every_check_carries_a_status_and_a_reason() -> None:
    checks = doctor.run("127.0.0.1", _free_port())
    names = {check.name for check in checks}
    assert {
        "data directory", "database", "port", "authentication",
        "credential vault", "GPU", "interface",
    } <= names
    assert all(check.status in {doctor.OK, doctor.WARN, doctor.FAIL} for check in checks)
    assert all(check.detail for check in checks)


def test_the_report_is_machine_readable() -> None:
    checks = doctor.run("127.0.0.1", _free_port())
    payload = json.loads(doctor.to_json(checks))
    assert [item["name"] for item in payload] == [check.name for check in checks]
    assert doctor.format_report(checks, "0.0.0").startswith("Tabularium 0.0.0")


def test_a_busy_port_is_a_failure_not_a_warning() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        check = doctor.check_port("127.0.0.1", port)
    finally:
        listener.close()
    assert check.status == doctor.FAIL
    assert check.hint


def test_more_than_one_worker_is_refused(monkeypatch) -> None:
    # The application keeps downloads, jobs and presence in process memory: a second
    # worker would duplicate them and split the sessions.
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    check = doctor.check_web_concurrency()
    assert check.status == doctor.FAIL
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    assert doctor.check_web_concurrency().status == doctor.OK


def test_a_missing_interface_is_a_failure(monkeypatch, tmp_path) -> None:
    from app import config

    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "missing")
    assert doctor.check_frontend().status == doctor.FAIL


def test_exit_code_only_reflects_failures() -> None:
    assert doctor.exit_code([doctor.Check("a", doctor.OK, "x")]) == 0
    assert doctor.exit_code([doctor.Check("a", doctor.WARN, "x")]) == 0
    assert doctor.exit_code([doctor.Check("a", doctor.FAIL, "x")]) == 1


def test_the_cli_refuses_more_than_one_worker(capsys) -> None:
    from app.cli import main

    assert main(["--workers", "2"]) == 2
    assert "must be 1" in capsys.readouterr().err


def test_the_app_refuses_to_start_with_two_workers(monkeypatch) -> None:
    from app.main import _assert_single_process

    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    try:
        _assert_single_process()
    except RuntimeError as exc:
        assert "single process" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("a multi-worker start was accepted")


def test_a_database_from_a_newer_release_names_the_way_back() -> None:
    from app import db
    from app.adapters import store

    db.init_db()
    store.put_raw("schema_version", str(int(db.SCHEMA_VERSION) + 1))
    try:
        check = doctor.check_schema()
    finally:
        store.put_raw("schema_version", db.SCHEMA_VERSION)
    assert check.status == doctor.FAIL
    assert "restore-db" in check.hint
