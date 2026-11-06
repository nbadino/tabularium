"""Layer di persistenza SQLite.

Schema con progetti, pagine, annotazioni e layer self-hosted. Le versioni
fino alla baseline sono assorbite dallo schema iniziale più le migrazioni
storiche per presenza di colonna; da lì in poi ogni cambiamento è una
migrazione esplicita e ordinata (v. ``_apply_migrations``).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from contextlib import closing

from . import config

SCHEMA_VERSION = "26"

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
    -- Primo accesso / password reimpostata dall'admin: l'utente deve sceglierne
    -- una propria prima di poter usare l'istanza (v. api/auth.py).
    must_change_password INTEGER NOT NULL DEFAULT 0,
    -- Invito via link: si conserva solo l'hash del token e la scadenza.
    invite_token_hash TEXT,
    invite_expires_at TEXT,
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
    ("16", """
        -- Un checkpoint prodotto dal fine-tuning può diventare un modello
        -- locale riutilizzabile. Manteniamo il riferimento al run e
        -- all'adapter base: prompt, OTSL e serializzazione restano quelli
        -- verificati dell'adapter, mentre il serving usa il checkpoint.
        ALTER TABLE custom_models ADD COLUMN source_path TEXT;
        ALTER TABLE custom_models ADD COLUMN base_adapter_id TEXT;
        ALTER TABLE custom_models ADD COLUMN source_run_id TEXT;
    """),
    ("17", """
        -- Una run LoRA produce un adapter, non un modello completo: il
        -- serving deve caricare il modello base e applicare questo adapter.
        ALTER TABLE custom_models ADD COLUMN source_train_type TEXT;
    """),
    ("18", """
        -- Onboarding e sicurezza account: password da cambiare al primo
        -- accesso (o dopo un reset dell'admin) e invito via link a scadenza.
        ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE users ADD COLUMN invite_token_hash TEXT;
        ALTER TABLE users ADD COLUMN invite_expires_at TEXT;
    """),
    ("19", """
        CREATE TABLE IF NOT EXISTS page_work (
            page_id INTEGER PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
            assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            reviewer_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            state TEXT NOT NULL DEFAULT 'annotation' CHECK(state IN ('annotation','pending','changes_requested','approved')),
            version INTEGER NOT NULL DEFAULT 1,
            submitted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            submitted_revision INTEGER,
            approved_revision INTEGER,
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(assignee_id IS NULL OR reviewer_id IS NULL OR assignee_id != reviewer_id)
        );
        CREATE INDEX IF NOT EXISTS idx_page_work_assignee ON page_work(assignee_id, state);
        CREATE INDEX IF NOT EXISTS idx_page_work_reviewer ON page_work(reviewer_id, state);
        CREATE TRIGGER IF NOT EXISTS invalidate_team_approval AFTER UPDATE OF annotation_revision ON pages
        WHEN NEW.annotation_revision != OLD.annotation_revision
          AND EXISTS(SELECT 1 FROM page_work WHERE page_id=NEW.id AND state='approved')
        BEGIN
            UPDATE page_work SET state='changes_requested', version=version+1,
                updated_at=datetime('now') WHERE page_id=NEW.id;
            UPDATE pages SET status='review' WHERE id=NEW.id AND status IN ('approved','exported');
        END;
    """),
    ("20", """
        -- LlamaIndex/LlamaCloud is a first-class remote OCR provider, not a
        -- generic OpenAI-compatible endpoint. Rebuild the CHECK constraint so
        -- active profiles keep their provider identity across restarts.
        PRAGMA foreign_keys = OFF;
        CREATE TABLE compute_profiles_v20 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL CHECK(provider IN ('local','ssh','vast','runpod','modal','custom','llamaindex')),
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
        INSERT INTO compute_profiles_v20 (
            id, name, provider, purpose, model_adapter_id, model_revision,
            served_model_name, endpoint, credential_ref, runtime_recipe_id,
            generation_profile_id, image_profile_id, hardware_profile_json,
            active, last_health_check, last_health_ok, last_health_error,
            created_at, updated_at
        )
        SELECT id, name, provider, purpose, model_adapter_id, model_revision,
               served_model_name, endpoint, credential_ref, runtime_recipe_id,
               generation_profile_id, image_profile_id, hardware_profile_json,
               active, last_health_check, last_health_ok, last_health_error,
               created_at, updated_at
          FROM compute_profiles;
        DROP TABLE compute_profiles;
        ALTER TABLE compute_profiles_v20 RENAME TO compute_profiles;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_active ON compute_profiles(active) WHERE active=1;
        PRAGMA foreign_keys = ON;
    """),
    ("21", """
        -- Collections group the pages of one project, typically by document type
        -- (docs/COLLECTIONS.md). A NULL collection_id is the declared state
        -- "no collection". SET NULL is only a safety net, the API always asks.
        -- Applied additively: a restored database can already have the column.
        CREATE TABLE IF NOT EXISTS collections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name        TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_name
            ON collections(project_id, name COLLATE NOCASE);
        ALTER TABLE pages ADD COLUMN collection_id INTEGER
            REFERENCES collections(id) ON DELETE SET NULL;
        CREATE INDEX IF NOT EXISTS idx_pages_collection ON pages(project_id, collection_id);
    """),
    ("22", """
        -- The page shown as a collection's cover in the page list. NULL is the
        -- declared state "no cover". Additive like 21.
        ALTER TABLE collections ADD COLUMN cover_page_id INTEGER
            REFERENCES pages(id) ON DELETE SET NULL;
    """),
    ("23", """
        -- SSO links a sign-in to the account holding the same email, so it must know
        -- who wrote that email: 1 = an administrator (or the provider itself), 0 = the
        -- account's owner (registration, own profile). Existing rows stay linkable
        -- (1): marking them 0 would lock every current user out of SSO at upgrade.
        ALTER TABLE users ADD COLUMN email_confirmed INTEGER NOT NULL DEFAULT 1;
    """),
    ("24", """
        -- A database that ran the pre-release consensus build recorded that
        -- table set as version 23 and so never received the users column of
        -- the real migration 23 above. Adding it here (skipped when present)
        -- brings such a database level with every other one.
        ALTER TABLE users ADD COLUMN email_confirmed INTEGER NOT NULL DEFAULT 1;

        -- Consensus: the same pages sent to several models (saved compute
        -- profiles) to see where they disagree. Each model's blocks are kept
        -- here, never in `blocks`/`tables`: a comparison must not touch the
        -- annotations. The profile name and model are copied so a run still
        -- reads after its profile is deleted.
        CREATE TABLE IF NOT EXISTS consensus_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            state TEXT NOT NULL CHECK(state IN (
                'queued','running','finished','finished_with_errors','failed','cancelled'
            )),
            models_json TEXT NOT NULL DEFAULT '[]',
            error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            started_at TEXT,
            ended_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_consensus_runs_project
            ON consensus_runs(project_id, created_at);

        CREATE TABLE IF NOT EXISTS consensus_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES consensus_runs(id) ON DELETE CASCADE,
            page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
            model_key TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN (
                'queued','running','finished','failed','cancelled'
            )),
            blocks_json TEXT NOT NULL DEFAULT '[]',
            error TEXT,
            started_at TEXT,
            ended_at TEXT,
            UNIQUE(run_id, page_id, model_key)
        );
        CREATE INDEX IF NOT EXISTS idx_consensus_outputs_page
            ON consensus_outputs(page_id, run_id);
    """),
    ("25", """
        -- Consensus decisions, shared by everyone on the project: what a person
        -- settled in a comparison (an area fine as it is, a model's reading taken,
        -- their own reading confirmed, a column confirmed at once, a model's table
        -- added, a model's reading adopted for the page), with the value before
        -- and after so it can be reverted. A revert or a comment is a row too
        -- (about_id names the decision it is about). The settled state of an
        -- area is derived from these rows, never kept in a browser.
        CREATE TABLE IF NOT EXISTS consensus_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES consensus_runs(id) ON DELETE CASCADE,
            page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
            cluster_id INTEGER,
            block_id INTEGER REFERENCES blocks(id) ON DELETE SET NULL,
            kind TEXT NOT NULL CHECK(kind IN (
                'resolved','reopened','use_model','removed','confirm_mine',
                'group_confirm','add_table','adopt_page','revert','comment'
            )),
            model_key TEXT,
            before_json TEXT NOT NULL DEFAULT 'null',
            after_json TEXT NOT NULL DEFAULT 'null',
            comment TEXT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            about_id INTEGER REFERENCES consensus_decisions(id) ON DELETE SET NULL,
            reverted_by INTEGER REFERENCES consensus_decisions(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_consensus_decisions_page
            ON consensus_decisions(page_id, run_id, id);
    """),
    ("26", """
        -- A saved model's API key belongs to a user, not to the model: each user
        -- adds their own (Recognition, Saved models) and a run uses the key of the
        -- user who starts it. The ciphertext is the vault's (Fernet,
        -- TABULARIUM_VAULT_KEY). `key_required` marks the models that reach a paid
        -- API: without a key of their own, a user cannot run them.
        ALTER TABLE compute_profiles ADD COLUMN key_required INTEGER NOT NULL DEFAULT 0;
        CREATE TABLE IF NOT EXISTS user_model_keys (
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            profile_id INTEGER NOT NULL REFERENCES compute_profiles(id) ON DELETE CASCADE,
            ciphertext TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, profile_id)
        );
        UPDATE compute_profiles SET key_required=1
         WHERE name != 'legacy-active'
           AND (COALESCE(credential_ref, '') != ''
                OR model_adapter_id IN ('gemini-argon', 'llamaindex-ocria-1')
                OR provider = 'llamaindex');
        -- The key a saved model carried was the key of the admin who saved it (the
        -- audit trail names them, else the first active admin). It moves, still
        -- encrypted, to that admin's own row. With no user to give it to (a local,
        -- single-user database) it stays on the profile, read only in local mode.
        INSERT OR IGNORE INTO user_model_keys (user_id, profile_id, ciphertext)
        SELECT owner, id, ciphertext FROM (
            SELECT p.id AS id, s.ciphertext AS ciphertext, COALESCE(
                (SELECT a.actor_id FROM audit_events a JOIN users u ON u.id = a.actor_id
                  WHERE a.action = 'compute_profile.created'
                    AND a.resource_type = 'compute_profile' AND a.resource_id = p.id
                  ORDER BY a.id LIMIT 1),
                (SELECT u.id FROM users u WHERE u.role = 'admin' AND u.active = 1
                  ORDER BY u.id LIMIT 1)
            ) AS owner
              FROM compute_profiles p
              JOIN secrets s ON 'vault:' || s.name = p.credential_ref
             WHERE p.name != 'legacy-active'
               AND p.credential_ref LIKE 'vault:consensus_profile_%'
        ) WHERE owner IS NOT NULL;
        DELETE FROM secrets
         WHERE 'vault:' || name IN (
            SELECT credential_ref FROM compute_profiles
             WHERE credential_ref LIKE 'vault:consensus_profile_%'
               AND id IN (SELECT profile_id FROM user_model_keys));
        UPDATE compute_profiles SET credential_ref = NULL
         WHERE credential_ref LIKE 'vault:consensus_profile_%'
           AND id IN (SELECT profile_id FROM user_model_keys);
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
        if version in {"16", "17", "18", "21", "22", "23", "24", "25", "26"}:
            _execute_additive_migration(conn, ddl)
            conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))
            conn.commit()
        else:
            _run_migration(conn, version, ddl)
        current = int(version)
    if current != int(SCHEMA_VERSION):
        raise RuntimeError(
            f"schema incompleto: il DB è alla versione {current} ma il codice "
            f"attende {SCHEMA_VERSION}; manca una migrazione in _MIGRATIONS"
        )


_FK_PRAGMA = re.compile(r"(?mi)^\s*PRAGMA\s+foreign_keys\s*=\s*(?:ON|OFF)\s*;\s*$")


def _run_migration(conn: sqlite3.Connection, version: str, ddl: str) -> None:
    """Apply one migration and record its version in the same transaction.

    `executescript` commits statement by statement, so a crash between the DDL and
    the version update re-ran a non-idempotent migration at the next start. The
    foreign-key PRAGMA is a no-op inside a transaction, so it is lifted out of it.
    """
    fk_off = bool(_FK_PRAGMA.search(ddl))
    body = _FK_PRAGMA.sub("", ddl)
    if fk_off:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(
            f"BEGIN;\n{body}\n;UPDATE meta SET value='{int(version)}' WHERE key='schema_version';\nCOMMIT;"
        )
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        if fk_off:
            conn.execute("PRAGMA foreign_keys = ON")


def _execute_additive_migration(conn: sqlite3.Connection, ddl: str) -> None:
    """Esegue una migrazione mantenendo ``ADD COLUMN`` idempotente.

    I database ripristinati possono avere lo schema più recente della versione
    registrata in ``meta``. SQLite non supporta ``ADD COLUMN IF NOT EXISTS``;
    perciò ignoriamo solo un'aggiunta già presente e lasciamo propagare ogni
    altro errore.
    """
    for statement in ddl.split(";"):
        sql = re.sub(r"(?m)^\s*--[^\n]*(?:\n|$)", "", statement).strip()
        if not sql:
            continue
        match = re.match(
            r"ALTER\s+TABLE\s+([\w\"]+)\s+ADD\s+COLUMN\s+([\w\"]+)",
            sql,
            re.IGNORECASE,
        )
        if not match:
            conn.execute(sql)
            continue
        table = match.group(1).strip('"')
        column = match.group(2).strip('"')
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(sql)


def _has_tables() -> bool:
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone() is not None


def _stored_version_before_changes() -> int | None:
    """The schema version on disk, read before init_db writes anything."""
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError:  # no meta table: older than the versioning
            return None
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def _guard_upgrade() -> None:
    """Refuse a database from a newer release; back up one that is about to migrate.

    Migrations only go forward, so the copy taken here is the way back (R9).
    """
    if not _has_tables():
        return  # an empty file, created before the first init: nothing to protect
    stored = _stored_version_before_changes()
    if stored is not None and stored > int(SCHEMA_VERSION):
        raise RuntimeError(
            f"the database is at schema version {stored}, newer than this release "
            f"({SCHEMA_VERSION}): a later Tabularium upgraded it. Start that release, or "
            "restore the backup taken before the upgrade (docs/guide/upgrading.md)."
        )
    if stored is None or stored < int(SCHEMA_VERSION):
        from .services import backup

        backup.create_backup(reason=f"pre-migration-v{stored if stored is not None else 'legacy'}")


# Columns that store absolute paths, some of them under the data root.
_ROOT_PATH_COLUMNS = (
    ("projects", "root_dir"),
    ("projects", "archive_dir"),
    ("pages", "abs_path"),
    ("pages", "thumb_path"),
    ("jobs", "log_path"),
)


def _follow_data_root(conn: sqlite3.Connection) -> None:
    """Rewrite stored paths under the previous data root when the root has moved.

    Paths are stored absolute; without this, a data root copied or moved to a new
    place keeps pointing at the old one and pages lose their images.
    shortcut: paths inside jobs.command_json stay as they were; they only describe
    past runs.
    """
    root = str(config.ROOT_DIR)
    row = conn.execute("SELECT value FROM meta WHERE key='data_root'").fetchone()
    old = row["value"] if row else None
    if old and old != root:
        conn.commit()
        from .services import backup

        backup.create_backup(reason="pre-relocation")
        sep_old = old.rstrip("/\\") + os.sep
        cut = len(sep_old) - 1
        for table, column in _ROOT_PATH_COLUMNS:
            conn.execute(
                f"UPDATE {table} SET {column} = ? || substr({column}, ?) "
                f"WHERE {column} = ? OR substr({column}, 1, ?) = ?",
                (root, cut + 1, old, len(sep_old), sep_old),
            )
        for page in conn.execute(
            "SELECT id, meta_json FROM pages WHERE meta_json LIKE '%extracted_path%'"
        ).fetchall():
            try:
                meta = json.loads(page["meta_json"] or "{}")
            except ValueError:
                continue
            path = meta.get("extracted_path")
            if isinstance(path, str) and path.startswith(sep_old):
                meta["extracted_path"] = root + path[cut:]
                conn.execute("UPDATE pages SET meta_json=? WHERE id=?", (json.dumps(meta), page["id"]))
        logging.getLogger("tabularium").warning("data root moved from %s to %s: stored paths updated", old, root)
    if old != root:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('data_root', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (root,),
        )


def init_db() -> None:
    config.ensure_dirs()
    new_database = not config.DB_PATH.exists()
    if not new_database:
        _guard_upgrade()
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
        _follow_data_root(conn)
