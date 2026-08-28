#!/usr/bin/env bash
# Installa/rimuove l'avvio automatico per l'utente Linux corrente.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/tabularium.desktop"

if [ "${1:-}" = "--remove" ]; then
  rm -f "$DESKTOP_FILE"
  echo ">> Avvio automatico Tabularium disattivato."
  exit 0
fi

mkdir -p "$AUTOSTART_DIR"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Tabularium
Comment=Avvia Tabularium e apre la dashboard locale
Exec=$ROOT/scripts/autostart.sh
Terminal=false
StartupNotify=false
OnlyShowIn=KDE;
X-KDE-autostart-after=panel
EOF
chmod +x "$ROOT/scripts/autostart.sh"
echo ">> Avvio automatico Tabularium installato: $DESKTOP_FILE"
