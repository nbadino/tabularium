"""Layer di persistenza SQLite.

Schema con progetti, pagine, annotazioni e layer self-hosted. Le versioni
fino alla baseline sono assorbite dallo schema iniziale più le migrazioni
storiche per presenza di colonna; da lì in poi ogni cambiamento è una
migrazione esplicita e ordinata (v. ``_apply_migrations``).
"""
from __future__ import annotations

import sqlite3

from . import config

SCHEMA_VERSION = "15"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    root_dir      TEXT    NOT NULL,
    archive_dir   TEXT,
    settings_json TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path    TEXT    NOT NULL,
    abs_path    TEXT    NOT NULL,
    source_kind TEXT    NOT NULL DEFAULT 'image',
    pdf_page    INTEGER,
    width       INTEGER NOT NULL DEFAULT 0,
    height      INTEGER NOT NULL DEFAULT 0,
    issue_date  TEXT,
    issue_no    TEXT,
    page_no     TEXT,
    page_type   TEXT,
    status      TEXT    NOT NULL DEFAULT 'new',
    thumb_path  TEXT,
    meta_json   TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pages_project ON pages(project_id);

CREATE TABLE IF NOT EXISTS blocks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id        INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    label          TEXT    NOT NULL,
    kind           TEXT    NOT NULL DEFAULT 'rect',
    points         TEXT    NOT NULL DEFAULT '[]',
    content        TEXT    NOT NULL DEFAULT '',
    order_idx      INTEGER,
    prefill_source TEXT,
    confirmed      INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_blocks_page  ON blocks(page_id);
CREATE INDEX IF NOT EXISTS idx_blocks_order ON blocks(page_id, order_idx);

CREATE TABLE IF NOT EXISTS tables (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id   INTEGER NOT NULL UNIQUE REFERENCES blocks(id) ON DELETE CASCADE,
    grid_json  TEXT    NOT NULL DEFAULT '{}',
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS page_reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id    INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    reviewer   TEXT NOT NULL DEFAULT 'local',
    status     TEXT NOT NULL CHECK(status IN ('pending','pass','fail')),
    errors_json TEXT NOT NULL DEFAULT '[]',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_page_reviews_page ON page_reviews(page_id);

-- Self-hosted: utenti, sessioni, proprietà e membri dei progetti (v6).
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    email         TEXT,
    password_hash TEXT    NOT NULL,
    salt          TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'editor'
                  CHECK(role IN ('admin','editor','viewer')),
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT    NOT NULL UNIQUE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT    NOT NULL,
    ip         TEXT,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL DEFAULT 'editor'
               CHECK(role IN ('editor','viewer')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, user_id)
);
"""

# Migrazioni additive storiche (versioni ≤ 6): colonne aggiunte in corso
# d'opera, applicate per *presenza* (idempotenti, ordine libero).
_LEGACY_COLUMN_MIGRATIONS = [
    ("projects", "archive_dir", "ALTER TABLE projects ADD COLUMN archive_dir TEXT"),
    # Self-hosted: ogni progetto ha un proprietario (NULL per i progetti esistenti:
    # vengono assegnati all'amministratore al primo setup, v. services/auth.py).
    ("projects", "owner_id", "ALTER TABLE projects ADD COLUMN owner_id INTEGER"),
    # SQLite non offre `ADD COLUMN IF NOT EXISTS`. La migrazione per presenza
    # rende l'aggiunta idempotente anche nei test/restore che riallineano la
    # versione registrata lasciando intatte le tabelle più nuove.
    (
        "blocks",
        "recognition_run_id",
        "ALTER TABLE blocks ADD COLUMN recognition_run_id INTEGER "
        "REFERENCES recognition_runs(id) ON DELETE SET NULL",
    ),
]

# La versione 6 è la *baseline*: le versioni precedenti sono assorbite dallo
# schema iniziale + _ensure_columns. Da qui in poi ogni cambiamento di schema
# è una migrazione esplicita e ordinata: (versione, DDL), applicate una volta
# sola in ordine crescente alle sole basi più indietro di esse.
BASELINE_VERSION = 6

# Migrazioni versionate dalla 7 in poi. Esempio della forma attesa:
#   _MIGRATIONS = [("7", "ALTER TABLE pages ADD COLUMN rotation_deg REAL NOT NULL DEFAULT 0")]
_MIGRATIONS: list[tuple[str, str]] = [
    # Optimistic concurrency for the page-wide annotation autosave. Keeping
    # the counter on pages makes the whole annotation snapshot one revision.
    ("7", "ALTER TABLE pages ADD COLUMN annotation_revision INTEGER NOT NULL DEFAULT 0"),
    ("8", """
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            resource_type TEXT,
            resource_id INTEGER,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_audit_resource
            ON audit_events(resource_type, resource_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_actor
            ON audit_events(actor_id, created_at);
    """),
    ("9", """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            provider TEXT NOT NULL DEFAULT 'local',
            pid INTEGER,
            process_group INTEGER,
            remote_job_id TEXT,
            state TEXT NOT NULL,
            heartbeat_at TEXT,
            command_json TEXT NOT NULL DEFAULT '{}',
            log_path TEXT,
            cost_json TEXT NOT NULL DEFAULT '{}',
            recovery_strategy TEXT,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            ended_at TEXT,
            exit_code INTEGER,
            error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, heartbeat_at);
    """),
    ("10", """
        CREATE TABLE IF NOT EXISTS compute_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL CHECK(provider IN ('local','ssh','vast','runpod','modal','custom')),
            purpose TEXT NOT NULL CHECK(purpose IN ('inference','training','both')),
            model_adapter_id TEXT NOT NULL,
            model_revision TEXT,
            served_model_name TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            credential_ref TEXT,
            runtime_recipe_id TEXT,
            generation_profile_id TEXT,
            image_profile_id TEXT,
            hardware_profile_json TEXT NOT NULL DEFAULT '{}',
            active INTEGER NOT NULL DEFAULT 0,
            last_health_check TEXT,
            last_health_ok INTEGER,
            last_health_error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_active ON compute_profiles(active) WHERE active=1;
    """),
    ("11", """
        CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT PRIMARY KEY,
            window_started REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0
        );
    """),
    ("12", """
        -- `owner_id` was introduced additively in the v6 baseline, before
        -- projects had a real FK. Rebuild the table so old and new databases
        -- share the same ownership invariant. NULL remains valid for legacy
        -- projects in auth-off mode; a non-NULL value must reference users.
        PRAGMA foreign_keys = OFF;
        CREATE TABLE projects_owner_fk_new (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            root_dir      TEXT    NOT NULL,
            archive_dir   TEXT,
            settings_json TEXT    NOT NULL DEFAULT '{}',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            owner_id      INTEGER REFERENCES users(id) ON DELETE RESTRICT
        );
        INSERT INTO projects_owner_fk_new
            (id, name, root_dir, archive_dir, settings_json, created_at, owner_id)
        SELECT id, name, root_dir, archive_dir, settings_json, created_at, owner_id
          FROM projects;
        DROP TABLE projects;
        ALTER TABLE projects_owner_fk_new RENAME TO projects;
        CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id);
        PRAGMA foreign_keys = ON;
    """),
    ("13", """
        CREATE TABLE IF NOT EXISTS secrets (
            name TEXT PRIMARY KEY,
            ciphertext TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """),
    ("14", """
        -- Modelli aggiunti liberamente dall'utente (un repo Hugging Face
        -- qualsiasi, non uno degli adapter con protocollo OCR verificato):
        -- solo download + serve generico via `vllm serve`, come in LM Studio.
        -- L'`id` è l'adapter_id usato ovunque nel registro modelli (prefisso
        -- 'custom-' per non collidere mai con gli id fissi di model_adapters.py).
        CREATE TABLE IF NOT EXISTS custom_models (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            hf_repo TEXT NOT NULL,
            hf_revision TEXT,
            served_model_name TEXT NOT NULL,
            trust_remote_code INTEGER NOT NULL DEFAULT 0,
            max_model_len INTEGER,
            gpu_memory_utilization REAL,
            extra_args TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """),
    ("15", """
        -- Una run di riconoscimento è indipendente dalla connessione del
        -- browser: conserva configurazione, avanzamento e risultato per
        -- pagina, così il compute può essere disattivato prima della review.
        CREATE TABLE IF NOT EXISTS recognition_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            state TEXT NOT NULL CHECK(state IN (
                'queued','running','finished','finished_with_errors','failed','cancelled'
            )),
            engine TEXT NOT NULL CHECK(engine IN ('model','ocr')),
            mode TEXT NOT NULL DEFAULT 'replace_drafts',
            model_mode TEXT NOT NULL DEFAULT 'native',
            model_name TEXT,
            adapter_id TEXT,
            provider TEXT NOT NULL DEFAULT 'local',
            endpoint TEXT,
            stop_policy TEXT NOT NULL DEFAULT 'none'
                CHECK(stop_policy IN ('none','disable_inference')),
            total_pages INTEGER NOT NULL DEFAULT 0,
            completed_pages INTEGER NOT NULL DEFAULT 0,
            succeeded_pages INTEGER NOT NULL DEFAULT 0,
            failed_pages INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            heartbeat_at TEXT,
            ended_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_recognition_runs_project
            ON recognition_runs(project_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_recognition_runs_state
            ON recognition_runs(state, heartbeat_at);

        CREATE TABLE IF NOT EXISTS recognition_run_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES recognition_runs(id) ON DELETE CASCADE,
            page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
            state TEXT NOT NULL CHECK(state IN (
                'queued','running','finished','failed','cancelled'
            )),
            detected INTEGER NOT NULL DEFAULT 0,
            inserted INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            started_at TEXT,
            ended_at TEXT,
            UNIQUE(run_id, page_id)
        );
        CREATE INDEX IF NOT EXISTS idx_recognition_items_run
            ON recognition_run_items(run_id, state, id);

        CREATE INDEX IF NOT EXISTS idx_blocks_recognition_run
            ON blocks(recognition_run_id);
    """),
]


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    # `timeout` (secondi di attesa prima di sollevare "database is locked")
    # più `busy_timeout` (equivalente lato SQLite, in ms): il default di
    # sqlite3 aspetta troppo poco sotto richieste concorrenti reali — questo
    # backend ha più cicli di polling attivi in parallelo (stato Modal ogni
    # 2s, stato serve dei modelli ogni 3s) oltre alle richieste utente.
    # `journal_mode=WAL` viene configurato una sola volta in `init_db`: su
    # ogni connessione è un'operazione di scrittura e può bloccare l'avvio
    # proprio mentre un'altra richiesta sta leggendo il database.
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Migrazioni storiche per presenza di colonna (versioni ≤ baseline)."""
    for table, column, ddl in _LEGACY_COLUMN_MIGRATIONS:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(ddl)


def _stored_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return None


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Porta il DB alla versione corrente, una migrazione alla volta.

    Sequenza per ogni migrazione: leggi versione → applica le sole migrazioni
    successive → aggiorna la versione registrata. Niente più sovrascrittura
    incondizionata: la versione in ``meta`` racconta davvero dove sta il DB.
    """
    stored = _stored_version(conn)
    if stored is None:
        # O il DB è appena nato (lo schema è già quello corrente) oppure è
        # precedente alla registrazione della versione: in entrambi i casi
        # _ensure_columns ha già allineato le colonne note, quindi si parte
        # dalla baseline e si applicano solo le migrazioni successive.
        stored = BASELINE_VERSION
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(BASELINE_VERSION),),
        )
    current = stored
    for version, ddl in sorted(_MIGRATIONS, key=lambda m: int(m[0])):
        if int(version) <= current:
            continue
        conn.executescript(ddl)
        current = int(version)
        conn.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'",
            (str(current),),
        )
    if current != int(SCHEMA_VERSION):
        raise RuntimeError(
            f"schema incompleto: il DB è alla versione {current} ma il codice "
            f"attende {SCHEMA_VERSION}; manca una migrazione in _MIGRATIONS"
        )


def init_db() -> None:
    config.ensure_dirs()
    new_database = not config.DB_PATH.exists()
    with connect() as conn:
        # Bootstrap soltanto: su un DB già esistente la PRAGMA è una scrittura
        # che può restare in attesa durante un'altra lettura. Il database
        # corrente è già WAL; per i nuovi DB la abilitiamo prima dello schema.
        if new_database:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        _ensure_columns(conn)
        # Anche le installazioni migrate devono avere lo stesso comportamento
        # concorrente: il journal mode è una proprietà del file, non del
        # processo che lo ha creato.
        if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            conn.execute("PRAGMA journal_mode = WAL")
        # Unicità per progetto su (abs_path, pagina). Con COALESCE perché NULL
        # non collide nelle UNIQUE index di SQLite (per le immagini pdf_page = NULL).
        conn.execute("DROP INDEX IF EXISTS idx_pages_unique")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pages_unique "
            "ON pages(project_id, abs_path, COALESCE(pdf_page, -1))"
        )
        _apply_migrations(conn)
