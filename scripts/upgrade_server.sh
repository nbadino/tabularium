#!/usr/bin/env bash
# Upgrade a server that runs Tabularium from a git checkout under systemd.
#
#   sudo scripts/upgrade_server.sh --check      # is there a new release? (exit 10 = yes)
#   sudo scripts/upgrade_server.sh              # upgrade to the latest release tag
#   sudo scripts/upgrade_server.sh --to main    # or to a branch, tag or commit
#
# What it does, in order, and why it is safe for the data:
#   1. refuses to start on a dirty checkout, or when the target would drop local
#      commits (it only ever fast-forwards: nothing of yours is overwritten);
#   2. writes a full backup (database + projects + vault key) with
#      `tabularium backup` BEFORE touching anything, and keeps the last few;
#   3. stops the service, updates the code, the Python dependencies and the
#      frontend build, starts the service (the app backs up and migrates the
#      database itself at startup);
#   4. waits for /api/health to answer with the new version;
#   5. if anything in 3-4 fails, it puts the previous code back, restores the
#      database copy taken just before the migration and starts the old release.
#
# Configuration comes from the systemd unit (user, data root, port); every value
# can be overridden with the environment variables listed under "Settings".
# The full procedure, by hand too, is in docs/guide/upgrading.md.
set -Eeuo pipefail

# ------------------------------------------------------------------ Settings --
SERVICE=${TABULARIUM_SERVICE:-tabularium}
REPO=${TABULARIUM_REPO:-"$(cd "$(dirname "$0")/.." && pwd)"}
BACKUP_DIR=${TABULARIUM_UPGRADE_BACKUP_DIR:-/var/backups/tabularium}
KEEP_BACKUPS=${TABULARIUM_UPGRADE_KEEP:-3}
HEALTH_TIMEOUT=${TABULARIUM_UPGRADE_HEALTH_TIMEOUT:-300}

unit_env() {  # value of VAR in the unit's Environment=, empty if absent
  systemctl show "$SERVICE" -p Environment --value 2>/dev/null | tr ' ' '\n' | sed -n "s/^$1=//p" | head -1
}
SVC_USER=${TABULARIUM_USER:-"$(systemctl show "$SERVICE" -p User --value 2>/dev/null)"}
SVC_USER=${SVC_USER:-tabularium}
DATA=${TABULARIUM_ROOT:-"$(unit_env TABULARIUM_ROOT)"}
PORT=${TABULARIUM_PORT:-"$(unit_env TABULARIUM_PORT)"}
PORT=${PORT:-8787}
SVC_HOME=$(getent passwd "$SVC_USER" | cut -d: -f6)
PY="$REPO/backend/.venv/bin/python"

TARGET=""
CHECK_ONLY=0
ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --to) TARGET=${2:?--to needs a branch, tag or commit}; shift ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\033[1m>> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mxx %s\033[0m\n' "$*" >&2; exit 1; }
as_service() { sudo -u "$SVC_USER" env HOME="$SVC_HOME" TABULARIUM_ROOT="$DATA" "$@"; }
git_() { git -C "$REPO" "$@"; }

health_version() {
  curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/health" 2>/dev/null \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null || true
}

# --------------------------------------------------------------- Preflight --
[ -d "$REPO/.git" ] || die "$REPO is not a git checkout (set TABULARIUM_REPO)"
[ -x "$PY" ] || die "no backend virtualenv at $PY"
if [ "$CHECK_ONLY" -eq 0 ]; then
  [ "$(id -u)" -eq 0 ] || die "run it with sudo: it stops and starts the $SERVICE service"
  [ -n "$DATA" ] && [ -f "$DATA/tabularium.db" ] || die "data root not found (TABULARIUM_ROOT=$DATA)"
fi

git_ fetch --quiet --tags --prune origin || die "git fetch failed (network? credentials?)"

CURRENT_VERSION=$(cat "$REPO/VERSION")
LATEST_TAG=$(git_ tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -1)
TARGET=${TARGET:-$LATEST_TAG}
[ -n "$TARGET" ] || die "no release tag found on origin"
# A branch name means its remote copy: `main` is what GitHub has, not the local branch.
if git_ rev-parse --verify --quiet "refs/remotes/origin/$TARGET" >/dev/null; then
  TARGET_REF="origin/$TARGET"
else
  TARGET_REF="$TARGET"
fi
TARGET_SHA=$(git_ rev-parse --verify --quiet "$TARGET_REF^{commit}") || die "unknown target: $TARGET"
TARGET_VERSION=$(git_ show "$TARGET_SHA:VERSION" | tr -d '[:space:]')
HEAD_SHA=$(git_ rev-parse HEAD)

echo "installed : $CURRENT_VERSION ($(git_ rev-parse --short HEAD) on $(git_ rev-parse --abbrev-ref HEAD))"
echo "target    : $TARGET_VERSION ($TARGET, $(git_ rev-parse --short "$TARGET_SHA"))"

if [ "$CHECK_ONLY" -eq 1 ]; then
  if [ "$HEAD_SHA" = "$TARGET_SHA" ] || git_ merge-base --is-ancestor "$TARGET_SHA" HEAD; then
    echo "up to date"; exit 0
  fi
  echo "update available: run  sudo $0${TARGET:+ --to $TARGET}"
  exit 10
fi

if [ "$HEAD_SHA" = "$TARGET_SHA" ] || git_ merge-base --is-ancestor "$TARGET_SHA" HEAD; then
  say "Already at $TARGET or newer: nothing to do."
  exit 0
fi
[ -z "$(git_ status --porcelain --untracked-files=no)" ] \
  || die "the checkout has uncommitted changes: commit or stash them first (git -C $REPO status)"
if ! git_ merge-base --is-ancestor HEAD "$TARGET_SHA"; then
  die "$(printf '%s\n' \
    "the checkout has commits that $TARGET does not contain:" \
    "$(git_ log --oneline "$TARGET_SHA..HEAD" | head -20)" \
    "Upgrading would drop them. Push them (a branch or a pull request), merge them into" \
    "$TARGET, or rebase them onto it, then run this again with --to <that branch>.")"
fi

echo
echo "Changes ($(git_ rev-list --count HEAD.."$TARGET_SHA") commits):"
git_ log --oneline --no-merges HEAD.."$TARGET_SHA" | head -15
if [ "$ASSUME_YES" -eq 0 ]; then
  read -r -p "Upgrade $CURRENT_VERSION -> $TARGET_VERSION now? The site is down for a minute or two. [y/N] " reply
  case "$reply" in y|Y|s|S|yes|si|sì) ;; *) echo "Nothing changed."; exit 0 ;; esac
fi

# ------------------------------------------------------------------ Backup --
mkdir -p "$BACKUP_DIR"
chown "$SVC_USER": "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
need_kb=$(du -sk --exclude=tiles --exclude='*-runtime' --exclude=cache "$DATA" | cut -f1)
free_kb=$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')
[ "$free_kb" -gt "$need_kb" ] || die "not enough space in $BACKUP_DIR for a backup (~$((need_kb/1024)) MB needed)"

STAMP=$(date +%Y%m%dT%H%M%S)
ARCHIVE="$BACKUP_DIR/tabularium-$CURRENT_VERSION-before-$TARGET_VERSION-$STAMP.tar.gz"
say "Backup of data and vault key -> $ARCHIVE (a few minutes for a large archive)"
(cd "$REPO/backend" && as_service "$PY" -m app.cli backup --data "$DATA" --out "$ARCHIVE") \
  || die "backup failed: nothing was changed"
tar -tzf "$ARCHIVE" >/dev/null || die "backup archive unreadable: nothing was changed"
echo "$HEAD_SHA" > "$BACKUP_DIR/last-upgrade-from.sha"
# Keep the newest archives only (they are full copies of the data).
ls -1t "$BACKUP_DIR"/tabularium-*-before-*.tar.gz 2>/dev/null | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -f --
MARK=$(mktemp)  # anything newer than this in backups/ was written by the new release

# ------------------------------------------------------------- Rollback --
rollback() {
  trap - ERR INT TERM
  warn "The upgrade failed: going back to $CURRENT_VERSION ($(git_ rev-parse --short "$HEAD_SHA"))."
  systemctl stop "$SERVICE" || true
  git_ reset --quiet --hard "$HEAD_SHA"
  "$PY" -m pip install --quiet -r "$REPO/backend/requirements.txt" || warn "pip reinstall failed"
  (cd "$REPO/frontend" && npm ci --no-audit --no-fund --silent && npm run build --silent) || warn "frontend rebuild failed"
  pre=$(find "$DATA/backups" -maxdepth 1 -name '*-pre-migration-*.db' -newer "$MARK" | sort | head -1)
  if [ -n "$pre" ]; then
    warn "The new release had migrated the database: restoring $(basename "$pre")."
    (cd "$REPO/backend" && as_service "$PY" -m app.cli restore-db --data "$DATA" --port "$PORT" --yes "$pre") \
      || warn "restore-db failed: restore $ARCHIVE by hand (docs/guide/upgrading.md)"
  fi
  systemctl start "$SERVICE" || true
  sleep 3
  warn "Back on $(health_version || echo '?'). Full backup kept in $ARCHIVE. Logs: journalctl -u $SERVICE -n 200"
  exit 1
}
trap rollback ERR INT TERM

# ----------------------------------------------------------------- Upgrade --
say "Stopping $SERVICE"
systemctl stop "$SERVICE"

say "Code -> $TARGET ($TARGET_VERSION)"
git_ merge --quiet --ff-only "$TARGET_SHA"

say "Python dependencies"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$REPO/backend/requirements.txt"

say "Frontend build"
(cd "$REPO/frontend" && npm ci --no-audit --no-fund --silent && npm run build --silent)

say "Starting $SERVICE (it backs up and migrates the database if needed)"
systemctl start "$SERVICE"
deadline=$((SECONDS + HEALTH_TIMEOUT))
until [ "$(health_version)" = "$TARGET_VERSION" ]; do
  state=$(systemctl is-active "$SERVICE" || true)  # "activating" between restarts is fine
  if [ $SECONDS -ge $deadline ] || [ "$state" = failed ] || [ "$state" = inactive ]; then
    warn "version $TARGET_VERSION did not come up healthy (service: $state)"
    journalctl -u "$SERVICE" -n 30 --no-pager >&2 || true
    false
  fi
  sleep 2
done
trap - ERR INT TERM
rm -f "$MARK"

say "Tabularium $TARGET_VERSION is running."
echo "Backup kept in: $ARCHIVE"
echo "Check:          sudo -u $SVC_USER env HOME=$SVC_HOME TABULARIUM_ROOT=$DATA $PY -m app.cli doctor --data $DATA  (from $REPO/backend)"
