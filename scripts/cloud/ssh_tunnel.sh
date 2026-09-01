#!/usr/bin/env bash
# ==============================================================================
# Tabularium — Local SSH Tunnel for Cloud Inference (Vast.ai / RunPod)
# ==============================================================================
# Crea un tunnel SSH sicuro e cifrato tra il tuo PC locale e l'istanza GPU cloud.
# In questo modo il backend Tabularium invia le richieste a http://127.0.0.1:8888/v1
# senza dover esporre porte pubbliche non protette su internet.
#
# Esempi d'uso:
#   1) Copiando la stringa SSH di Vast.ai:
#      ./scripts/cloud/ssh_tunnel.sh "ssh -p 34567 root@198.51.100.24 -L 8080:localhost:8080"
#   2) Passando host e porta (con host key già registrata):
#      ./scripts/cloud/ssh_tunnel.sh root@198.51.100.24 -p 34567
#   3) Specificando porta locale e remota personalizzata:
#      LOCAL_PORT=8888 REMOTE_PORT=8888 ./scripts/cloud/ssh_tunnel.sh root@198.51.100.24 -p 34567
# ==============================================================================
set -euo pipefail

LOCAL_PORT="${LOCAL_PORT:-8888}"
REMOTE_PORT="${REMOTE_PORT:-8888}"
KNOWN_HOSTS_FILE="${TABULARIUM_SSH_KNOWN_HOSTS:-${TABULARIUM_ROOT:-$PWD/data}/known_hosts}"

if [ $# -eq 0 ]; then
  echo "Uso: $0 <comando_ssh_o_host> [opzioni_ssh]"
  echo ""
  echo "Esempi:"
  echo "  $0 root@sshX.vast.ai -p 12345"
  echo "  $0 \"ssh -p 12345 root@sshX.vast.ai\""
  exit 1
fi

RAW_INPUT="$*"

# Estrai host e porta se l'utente ha incollato l'intero comando ssh
TARGET=""
SSH_ARGS=()

if [[ "$RAW_INPUT" =~ ssh[[:space:]]+(.*) ]]; then
  REST="${BASH_REMATCH[1]}"
  # Il comando incollato è input dell'utente: non valutarlo come shell code.
  read -r -a ARGS <<< "$REST"
  for ((i=0; i<${#ARGS[@]}; i++)); do
    arg="${ARGS[i]}"
    if [[ "$arg" == "-p" ]]; then
      SSH_ARGS+=("-p" "${ARGS[i+1]}")
      ((i++))
    elif [[ "$arg" == "-L"* ]]; then
      # salta eventuali -L passati nel comando grezzo, usiamo il nostro LOCAL_PORT:127.0.0.1:REMOTE_PORT
      if [[ "$arg" == "-L" ]]; then ((i++)); fi
    elif [[ "$arg" == -* ]]; then
      SSH_ARGS+=("$arg")
    else
      TARGET="$arg"
    fi
  done
else
  TARGET="$1"
  shift
  SSH_ARGS=("$@")
fi

echo "=========================================================="
echo ">> [Tabularium SSH Tunnel]"
echo ">> Target Cloud: $TARGET ${SSH_ARGS[*]:-}"
echo ">> Inoltro locale: http://127.0.0.1:$LOCAL_PORT -> Remoto: $REMOTE_PORT"
echo ">> Known hosts dedicato: $KNOWN_HOSTS_FILE"
echo "=========================================================="
echo ">> Connessione in corso... Premi Ctrl+C per interrompere."

mkdir -p "$(dirname "$KNOWN_HOSTS_FILE")"
touch "$KNOWN_HOSTS_FILE"
chmod 700 "$(dirname "$KNOWN_HOSTS_FILE")" 2>/dev/null || true
chmod 600 "$KNOWN_HOSTS_FILE" 2>/dev/null || true
echo ">> Host key verification: StrictHostKeyChecking=yes"

while true; do
  ssh -N \
    -o "ServerAliveInterval=20" \
    -o "ServerAliveCountMax=3" \
    -o "ExitOnForwardFailure=yes" \
    -o "StrictHostKeyChecking=yes" \
    -o "UserKnownHostsFile=$KNOWN_HOSTS_FILE" \
    -o "GlobalKnownHostsFile=/dev/null" \
    -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    "${SSH_ARGS[@]}" \
    "$TARGET" || {
      echo ">> Tunnel disconnesso. Riconnessione tra 3 secondi..."
      sleep 3
    }
done
