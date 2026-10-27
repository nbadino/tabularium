"""The dependency direction: pure logic in `domain/`, the outside world in `adapters/`.

Phase P3 of `docs/architecture/program.md`. Before it, a service imported the database,
so "change a setting" meant touching SQL, HTTP and the UI in one edit. The rule is one
sentence — domain code takes values and returns values — and this file is what makes it
a rule instead of a convention.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
DOMAIN = APP / "domain"

# Third-party or stdlib modules that mean "this module does I/O".
IO_MODULES = {
    "fastapi", "starlette", "uvicorn", "sqlite3", "httpx", "requests",
    "subprocess", "socket", "aiohttp",
}
# Application packages a pure module must not reach into.
LAYER_PREFIXES = ("app.services", "app.adapters", "app.api", "app.db", "app.main")


def _resolve(path: Path, node: ast.ImportFrom) -> str:
    """Absolute module path of a (possibly relative) import."""
    if node.level == 0:
        return node.module or ""
    parts = list(path.relative_to(APP.parent).with_suffix("").parts)  # ('app', 'domain', 'x')
    package = parts[:-1]
    base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
    return ".".join([*base, node.module]) if node.module else ".".join(base)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(_resolve(path, node))
    return found


def test_domain_has_no_io() -> None:
    offenders: list[str] = []
    for path in sorted(DOMAIN.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for module in _imports(path):
            root = module.split(".")[0]
            if root in IO_MODULES:
                offenders.append(f"{path.name}: importa {module}")
            elif module.startswith(LAYER_PREFIXES):
                offenders.append(f"{path.name}: importa {module} (strato applicativo)")
    assert not offenders, (
        "domain/ deve restare puro: valori in ingresso, valori in uscita.\n  "
        + "\n  ".join(offenders)
    )


def test_services_reach_the_database_only_through_adapters() -> None:
    # The two services that were migrated first, and the ones a regression would
    # reintroduce SQL into.
    for name in (
        "model_settings.py", "oidc.py", "prefill.py", "recognition.py",
        "dataset_builder.py", "evaluate.py", "blocks.py",
    ):
        path = APP / "services" / name
        source = path.read_text(encoding="utf-8")
        # Comments may quote SQL while explaining it; the code may not.
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        assert "from ..db import" not in code, f"{name}: importa il database direttamente"
        assert "SELECT " not in code and "INSERT INTO" not in code and "DELETE FROM" not in code, (
            f"{name}: contiene SQL; passa da app/adapters/"
        )


def test_domain_modules_are_actually_used() -> None:
    # A pure module nobody imports is dead weight pretending to be an architecture.
    assert sorted(path.stem for path in DOMAIN.glob("*.py") if path.name != "__init__.py") == [
        "inference", "otsl", "prefill", "table_headers", "table_similarity", "vram",
    ]


def test_no_name_is_defined_twice_in_the_same_scope() -> None:
    """A scripted move that cuts at the wrong marker silently shadows a method.

    Python keeps the last definition, so the tests pass while the file carries a dead
    copy of the old one. This happened during P4: a range replacement stopped at the
    first `headers = self._headers()` in the file, which belonged to another method.
    """
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scopes = [tree] + [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        for scope in scopes:
            names = [
                node.name for node in getattr(scope, "body", [])
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            ]
            duplicates = sorted({name for name in names if names.count(name) > 1})
            if duplicates:
                label = getattr(scope, "name", path.name)
                offenders.append(f"{path.relative_to(APP.parent)}::{label}: {duplicates}")
    assert not offenders, "definizioni duplicate nello stesso scope:\n  " + "\n  ".join(offenders)


def test_prefill_dispatches_by_declaration_not_by_model_identity() -> None:
    """The official pipeline is chosen by `native_runner`, never by `adapter_id`."""
    source = (APP / "services" / "prefill.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "adapter_id ==" not in code, (
        "prefill.py compares adapter ids: declare the capability on the adapter "
        "(native_runner) and dispatch on it"
    )


def _sql_statements(path: Path) -> int:
    """Count SQL keywords in code and non-docstring strings.

    Prose is excluded: a docstring saying "ready for a partial UPDATE" is not a query,
    and counting it would have kept `page_meta.py` on the pending list forever.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.update(range(body[0].lineno, body[0].end_lineno + 1))
    code = "\n".join(
        line
        for number, line in enumerate(source.splitlines(), start=1)
        if number not in docstrings and not line.lstrip().startswith("#")
    )
    return sum(
        code.count(marker)
        for marker in ("SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM")
    )


# P7.1: the API layer must not speak SQL. These modules are the migration in progress;
# the test below fails when one of them is cleaned, so the list cannot rot.
API_SQL_PENDING: set[str] = set()  # empty: `app/api/` is clean except `auth.py`

# Exceptions, with the reason each earns one. A name here is a decision, not a debt.
API_SQL_EXCEPTIONS: dict[str, str] = {
    "auth.py": "login, logout and session tokens: the API is the session boundary",
}


def test_the_api_layer_does_not_speak_sql() -> None:
    offenders = [
        path.name
        for path in sorted((APP / "api").glob("*.py"))
        if _sql_statements(path)
        and path.name not in API_SQL_PENDING
        and path.name not in API_SQL_EXCEPTIONS
    ]
    assert not offenders, (
        "query SQL nel livello HTTP: spostale in app/adapters/ o in un servizio. "
        f"Offendenti: {offenders}"
    )


def test_the_pending_api_modules_are_still_pending() -> None:
    """A name in the pending list must still need the migration.

    Without this, finishing `api/pages.py` would leave a stale exemption that
    silently allows the next query in.
    """
    still_dirty = {
        path.name for path in (APP / "api").glob("*.py") if _sql_statements(path)
    }
    assert API_SQL_PENDING <= still_dirty, (
        "questi moduli non hanno più SQL: toglili da API_SQL_PENDING: "
        f"{sorted(API_SQL_PENDING - still_dirty)}"
    )


# P7.1, second half: the same rule for `app/services/`. Three modules keep their SQL on
# purpose, each because the queries *are* the boundary. Everything else is a migration in
# progress, and the self-expiring test below forces the list to shrink as it is paid.
SERVICES_SQL_EXCEPTIONS: dict[str, str] = {
    "vault.py": "the encrypted store: the queries are the cryptography boundary",
    "auth.py": "sessions, passwords and tokens: the authentication boundary",
    "audit.py": "the trail must be written inside the caller's transaction",
}

# Empty: the migration is done. The mechanism stays, because the next service that needs
# an exemption must still name itself and say why.
SERVICES_SQL_PENDING: dict[str, str] = {}


def test_the_service_layer_does_not_speak_sql() -> None:
    offenders = [
        path.name
        for path in sorted((APP / "services").glob("*.py"))
        if _sql_statements(path)
        and path.name not in SERVICES_SQL_PENDING
        and path.name not in SERVICES_SQL_EXCEPTIONS
    ]
    assert not offenders, (
        "query SQL in app/services/: spostale in app/adapters/. "
        f"Offendenti: {offenders}"
    )


def test_the_pending_services_are_still_pending() -> None:
    still_dirty = {
        path.name for path in (APP / "services").glob("*.py") if _sql_statements(path)
    }
    assert set(SERVICES_SQL_PENDING) <= still_dirty, (
        "questi servizi non hanno più SQL: toglili da SERVICES_SQL_PENDING: "
        f"{sorted(set(SERVICES_SQL_PENDING) - still_dirty)}"
    )


def test_every_exception_carries_a_reason() -> None:
    for label, mapping in (
        ("API", API_SQL_EXCEPTIONS),
        ("services", SERVICES_SQL_EXCEPTIONS),
    ):
        for name, reason in mapping.items():
            assert reason.strip(), f"{label}: {name} has an empty reason"


def test_the_serving_dispatch_names_no_model() -> None:
    """A model with its own MLX runner declares `local_serve`; `serve_manager` obeys.

    The two exceptions that remain are MonkeyOCRv2's vLLM wrapper (its script and the
    DFlash draft are part of that model's documented serving, not a runtime choice) and
    the shared env-variable plumbing around it.
    """
    source = (APP / "services" / "serve_manager.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    for adapter_id in ("mineru2.5", "unlimited-ocr"):
        assert f'"{adapter_id}"' not in code, (
            f"serve_manager names {adapter_id}: declare `local_serve` on its recipe "
            "and dispatch on it"
        )

