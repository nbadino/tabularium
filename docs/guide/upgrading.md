# Upgrading and rollback

A new release replaces the program, never your data. This page says where the data
lives for each way of installing Tabularium, what an upgrade changes, and how to go
back if something goes wrong.

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
| `thumbs/`, `tiles/` | previews, and the PNG extracted from each PDF page at import |
| `models/` (or `TABULARIUM_MODELS_DIR`) | downloaded checkpoints |
| `*-runtime/`, `vendor/` | model runtimes prepared by the app; they can be rebuilt |
| `backups/` | database backups (the last 10, `TABULARIUM_BACKUP_RETENTION`) |
| `known_hosts`, `ssh/` | remote GPU host keys |

Three things to know before you touch anything:

- **The vault key is not in the database backups.** Without the same `.env`, the app
  still starts, but every saved API key or token is unreadable and must be entered
  again. `tabularium doctor` reports it on the `credential vault` line.
- **Page paths are absolute.** The database stores the full path of every scan, the
  uploaded ones under `projects/<id>/uploads/` included. If the data root ends up at a
  different absolute path, pages show no image. Keep the data root where it is; if
  you must move it, leave a symbolic link at the old path.
- **Scans imported from a folder on the server stay in that folder.** Tabularium
  records where they are and does not copy them: that folder must survive too.

## What an upgrade does

At startup the backend opens the database, applies the migrations it is missing, one
at a time, each in its own transaction with the version it reaches, then checks the
database integrity. A database that fails the check stops the startup with an error
instead of being used.

There is **no automatic backup before the migrations run**: take one yourself (step 1
below). Migrations only go forward: an older release refuses to start on a database
migrated by a newer one, so the way back is the backup you took before upgrading.

## Upgrade, step by step

### 1. Back up

1. In the app, as administrator: **Settings → Data and backups → Create backup**.
   This writes a consistent copy of the database to `<data root>/backups/` and is the
   file you restore if you roll back. Note its name.
2. Stop Tabularium (`Ctrl+C` in its terminal, or stop the container).
3. Copy the whole data root and `.env` somewhere else: an external disk, another
   machine. With the app stopped, a plain copy is consistent.

```bash
# installed with uv: the data root contains .env
tar -czf tabularium-backup-$(date +%F).tar.gz -C ~ Tabularium

# release archive or checkout: data/ and .env are both in the program folder
tar -czf tabularium-backup-$(date +%F).tar.gz -C /path/to/tabularium data .env

# container: stream the volume out (compose names it <project>_tabularium-data,
# see `docker volume ls`)
docker run --rm -v tabularium-data:/data:ro --entrypoint tar tabularium -czf - -C /data . \
  > tabularium-backup-$(date +%F).tar.gz
```

The database backup alone is not enough: it does not contain the scans, the crops,
the runs, the models or the vault key.

### 2. Install the new version

Pick the section for your installation.

**Installed with uv (from PyPI).** The data root is outside the package.

```bash
uv tool upgrade tabularium
```

If you installed from a wheel URL, install the new wheel over it:
`uv tool install --force https://github.com/nbadino/tabularium/releases/download/v<version>/tabularium-<version>-py3-none-any.whl`.

**Container.** The image is built from the sources; the data is in the volume, which
survives the new image.

```bash
cd tabularium                       # your clone
git fetch --tags && git checkout v<version>
docker compose -f docker/app/compose.yml up -d --build
```

With `docker run`: build `docker build -f docker/app/Dockerfile -t tabularium .`, then
`docker rm -f tabularium` and run it again with the **same** `-v tabularium-data:/data`.
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
while your data and key are in the old one. Do not move `data/` into the new folder:
its absolute path would change and the pages would lose their images. Instead:

1. Extract the new release next to the old one.
2. Copy the old `.env` into the new folder **before** running the setup (otherwise
   the setup generates a new, different key):
   `cp tabularium-<old>/.env tabularium-<new>/.env`
3. Point the new folder at the old data by adding one line to the new `.env`, with
   the absolute path:
   `TABULARIUM_ROOT=/absolute/path/to/tabularium-<old>/data`
4. Run `./scripts/setup_backend.sh` in the new folder (Windows:
   `scripts\setup_backend.ps1`).

From now on the old folder holds your data: do not delete it. To give the data a
version-free home, move it once and leave a symbolic link at the old path
(`mv tabularium-<old>/data ~/tabularium-data && ln -s ~/tabularium-data tabularium-<old>/data`),
then set `TABULARIUM_ROOT=~/tabularium-data` in `.env`. The same applies on Windows
with a directory junction (`mklink /J`).

### 3. Start it: the migrations run

Start Tabularium as usual (`tabularium`, `./scripts/run.sh`, or the container). The
migrations run during startup; if one fails, the backend stops with the error and
the database stays at the last version it reached.

### 4. Verify

```bash
tabularium doctor                   # installed
docker exec tabularium tabularium doctor     # container (compose: docker compose -f docker/app/compose.yml exec tabularium tabularium doctor)
cd backend && .venv/bin/python -m app.cli doctor   # release archive or checkout
```

Check that `schema` is `ok`, `database` is `ok` and `credential vault` does not
report unreadable credentials; while the app is running the `port` line fails, which
is expected. Then open the app, sign in, open a project and a page
with its image, and check that a saved provider still appears in **Models**.

## Rollback

Use this when the new version does not start or does not work for you. You need the
database backup from step 1 (or the full copy).

1. Stop Tabularium.
2. Reinstall the previous version:
    - uv: `uv tool install --force tabularium==<previous version>`
    - container: `git checkout v<previous version>`, then rebuild as in step 2
    - checkout: `git checkout v<previous version>`, then run the two setup scripts
    - release archive: start from the old folder again (its `.env` is still there;
      remove the `TABULARIUM_ROOT` line only if you added it there)
3. Put the pre-upgrade database back. Move the current database aside, do not
   delete it, then copy the backup in its place:

    ```bash
    cd <data root>
    mkdir -p pre-rollback && mv tabularium.db tabularium.db-wal tabularium.db-shm pre-rollback/ 2>/dev/null
    cp backups/<backup name>.db tabularium.db
    ```

    For the container, run the same commands inside the volume:

    ```bash
    docker run --rm -v tabularium-data:/data --entrypoint sh tabularium -c \
      'cd /data && mkdir -p pre-rollback && mv tabularium.db* pre-rollback/; cp backups/<backup name>.db tabularium.db'
    ```

4. Start the previous version and verify as in step 4.

Work done in the new version after the backup (new annotations, new pages) is not in
the restored database. Files it wrote to the data root stay on disk.

Do not roll back with **Settings → Data and backups → Restore** while the new version
is running: a restore migrates the restored copy to the running version at once. That
button is for undoing a mistake within the same version; it takes a `pre-restore`
backup first.

If the full copy from step 1 is all you have, stop the app, move the current data root
aside and extract the copy in its place, at the same absolute path, together with
`.env`.
