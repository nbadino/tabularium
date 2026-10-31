# Upgrading and rollback

A new release replaces the program, never your data. This page says where the data
lives for each way of installing Tabularium, what an upgrade does to it, and how to go
back if something goes wrong.

!!! note "Which release"
    `tabularium backup`, `tabularium restore-db`, the automatic backup before a
    migration, the relocation of stored paths and the published container image
    arrive with the release after 0.4.0. When you upgrade *from* 0.4.0 or earlier,
    your installed version does not have the backup command yet: use the manual
    copy shown in step 1.

## What you must keep

Everything Tabularium knows is in two places:

| What | Where | Why it matters |
|---|---|---|
| **Data root** | `~/Tabularium` (installed with uv or pip), `/data` (container), `<folder>/data` (release archive or source checkout); `TABULARIUM_ROOT` or `--data` overrides it | database, projects, uploaded scans, crops, thumbnails, runs, models, backups |
| **`.env`** with `TABULARIUM_VAULT_KEY` | inside the data root (installed, container); in the program folder, next to `scripts/` (release archive, checkout) | the key that decrypts the provider credentials saved from the interface |

Inside the data root:

| Path | Content |
|---|---|
| `tabularium.db` (plus `-wal`, `-shm` while running) | users, projects, pages, annotations, runs, encrypted credentials |
| `projects/<id>/` | uploaded scans (`uploads/`), crops, datasets, training runs (`runs/<run_id>/`) |
| `thumbs/` | previews, and the PNG extracted from each PDF page at import |
| `tiles/` | zoom tiles, regenerated on demand |
| `models/` (or `TABULARIUM_MODELS_DIR`) | downloaded checkpoints |
| `*-runtime/`, `vendor/`, `cache/` | model runtimes and caches the app prepares; they can be rebuilt |
| `backups/` | database backups (the last 10, `TABULARIUM_BACKUP_RETENTION`) |
| `pre-restore-<time>/` | a database set aside by `tabularium restore-db` |
| `known_hosts`, `ssh/` | remote GPU host keys |

Three things to know before you touch anything:

- **The vault key is not in the database backups.** Without the same `.env`, the app
  still starts, but every saved API key or token is unreadable and must be entered
  again. `tabularium doctor` reports it on the `credential vault` line.
  `tabularium backup` puts `.env` in the archive.
- **The database stores absolute paths.** At startup Tabularium compares the data
  root with the one it last ran from; if it moved, the paths under the old root are
  rewritten to the new one (after a `pre-relocation` backup). Releases up to 0.4.0
  do not do this: with them, keep the data root at the same absolute path.
- **Scans imported from a folder on the server stay in that folder.** Tabularium
  records where they are and does not copy them: that folder must survive too, at the
  same path, and it is not in any Tabularium backup.

## What an upgrade does

At startup the backend:

1. refuses a database written by a **newer** release, with a message that names the
   way back, before changing anything;
2. if the database is **behind**, copies it to
   `backups/tabularium-<time>-pre-migration-v<old version>.db`;
3. applies the missing migrations one at a time, each in its own transaction with the
   version it reaches;
4. checks the database integrity, and stops with an error if the check fails;
5. rewrites the stored paths if the data root has moved.

Migrations only go forward: the way back to an older release is the backup taken
before the upgrade.

## Upgrade, step by step

### 1. Back up

```bash
tabularium backup --out /somewhere/safe/tabularium-backup.tar.gz
```

The archive holds the data root under `data/` and the vault key as `.env`; the
database is copied consistently even if the app is running. Model runtimes, caches and
zoom tiles are left out, because the app rebuilds them. The archive contains the vault
key: keep it as private as the credentials it protects. From a release archive or a
checkout, the command is `backend/.venv/bin/python -m app.cli backup` (run it from
`backend/`). In a container:

```bash
docker exec tabularium tabularium backup --out /tmp/backup.tar.gz
docker cp tabularium:/tmp/backup.tar.gz ./tabularium-backup.tar.gz
```

**Without `tabularium backup`** (0.4.0 and earlier): stop Tabularium (`Ctrl+C` in its
terminal, or stop the container) and copy the data root and `.env` by hand. With the
app stopped, a plain copy is consistent.

```bash
# installed with uv: the data root contains .env
tar -czf tabularium-backup.tar.gz -C ~ Tabularium

# release archive or checkout: data/ and .env are both in the program folder
tar -czf tabularium-backup.tar.gz -C /path/to/tabularium data .env

# container: stream the volume out (compose names it <project>_tabularium-data,
# see `docker volume ls`)
docker run --rm -v tabularium-data:/data:ro --entrypoint tar tabularium -czf - -C /data . \
  > tabularium-backup.tar.gz
```

For a quick rollback point you can also use **Settings → Data and backups → Create
backup**: a consistent copy of the database alone in `<data root>/backups/`.

### 2. Install the new version

Stop Tabularium, then pick the section for your installation.

**Installed with uv (from PyPI).** The data root is outside the package.

```bash
uv tool upgrade tabularium
```

If you installed from a wheel URL, install the new wheel over it:
`uv tool install --force https://github.com/nbadino/tabularium/releases/download/v<version>/tabularium-<version>-py3-none-any.whl`.

**Container.** The data is in the volume, which survives a new image. Releases after
0.4.0 publish `ghcr.io/nbadino/tabularium:<version>`:

```bash
docker pull ghcr.io/nbadino/tabularium:<version>
docker rm -f tabularium
docker run -d --name tabularium -p 127.0.0.1:8787:8787 \
  -v tabularium-data:/data ghcr.io/nbadino/tabularium:<version>
```

Use the **same** volume name as before. With compose, or to build the image yourself
from a clone:

```bash
git fetch --tags && git checkout v<version>
docker compose -f docker/app/compose.yml up -d --build
```

Removing the container does not remove the volume; never run `docker volume rm` or
`docker compose down -v`.

**Source checkout.** `data/` and `.env` are ignored by git, so a pull does not touch
them.

```bash
git pull                            # or: git fetch --tags && git checkout v<version>
./scripts/setup_backend.sh          # updates the dependencies; keeps the existing key
./scripts/setup_frontend.sh
```

**Release archive.** Each release extracts into a new folder, `tabularium-<version>/`,
while your data and key are in the old one.

1. Extract the new release next to the old one.
2. Copy the old `.env` into the new folder **before** running the setup (otherwise
   the setup generates a new, different key):
   `cp tabularium-<old>/.env tabularium-<new>/.env`
3. Give the new folder the data, in one of two ways:
    - **Move it**, when the new release relocates stored paths (after 0.4.0):
      `mv tabularium-<old>/data tabularium-<new>/data`. The first start rewrites the
      paths.
    - **Point at it**, which works with every release: add the absolute path to the
      new `.env`, `TABULARIUM_ROOT=/absolute/path/to/tabularium-<old>/data`. The old
      folder then holds your data: do not delete it.
4. Run `./scripts/setup_backend.sh` in the new folder (Windows:
   `scripts\setup_backend.ps1`).

On a checkout or a release archive, `TABULARIUM_ROOT` moves the data but not the key:
`.env` is always read from the program folder.

### 3. Start it

Start Tabularium as usual (`tabularium`, `./scripts/run.sh`, or the container). The
startup takes the `pre-migration` backup, applies the migrations and, if the root
moved, rewrites the paths. If a migration fails, the backend stops with the error and
the database stays at the last version it reached.

### 4. Verify

```bash
tabularium doctor                                  # installed
docker exec tabularium tabularium doctor           # container
cd backend && .venv/bin/python -m app.cli doctor   # release archive or checkout
```

Check that `schema` is `ok`, `database` is `ok` and `credential vault` does not
report unreadable credentials; while the app is running the `port` line fails, which
is expected. Then open the app, sign in, open a project and a page with its image, and
check that a saved provider still appears in **Models**.

## Rollback

Use this when the new version does not start or does not work for you.

1. Stop Tabularium.
2. Put the pre-upgrade database back, **with the new version still installed**:

    ```bash
    tabularium restore-db tabularium-<time>-pre-migration-v<old version>.db --yes
    ```

    `tabularium restore-db` takes a file name from `<data root>/backups/` or a path,
    checks it, refuses while the app answers on its port, moves the current database
    to `<data root>/pre-restore-<time>/` and copies the backup in place without
    migrating it. `tabularium doctor` then reports the schema as behind: that is the
    point. Without `--yes` it only says what it would do. From a release archive or a
    checkout: `backend/.venv/bin/python -m app.cli restore-db …`. In a container, run
    it with the app stopped and the same volume:

    ```bash
    docker stop tabularium
    docker run --rm -v tabularium-data:/data --entrypoint tabularium \
      ghcr.io/nbadino/tabularium:<new version> restore-db <backup name> --yes
    ```

    `--entrypoint tabularium` is needed because the image's entry point starts the
    server.

3. Reinstall the previous version:
    - uv: `uv tool install --force tabularium==<previous version>`
    - container: run the previous image tag on the same volume
    - checkout: `git checkout v<previous version>`, then run the two setup scripts
    - release archive: start from the old folder again (put `data/` back there if you
      moved it)
4. Start it and verify as in step 4.

Work done in the new version after the backup (new annotations, new pages) is not in
the restored database. Files it wrote to the data root stay on disk.

Without `tabularium restore-db` (0.4.0 and earlier), do the same by hand with the app
stopped: move `tabularium.db`, `tabularium.db-wal` and `tabularium.db-shm` aside, never
delete them, and copy the backup to `tabularium.db`.

Do not roll back with **Settings → Data and backups → Restore**: a restore from the
interface migrates the restored copy to the running version at once. That button is
for undoing a mistake within the same version; it takes a `pre-restore` backup first.

### From a full archive

If the archive from `tabularium backup` is all you have, stop the app, move the
current data root aside and extract the archive:

```bash
mkdir restore && tar -xzf tabularium-backup.tar.gz -C restore
# restore/data is the data root, restore/.env the key
```

Put `restore/data` where the data root goes and `restore/.env` where `.env` goes (see
the first table). Tabularium rewrites the stored paths if the data root is now
somewhere else.
