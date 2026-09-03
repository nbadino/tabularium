#!/usr/bin/env bash
# Setup ambiente backend: venv + dipendenze (Python 3.11–3.13).
set -euo pipefail
cd "$(dirname "$0")/../backend" || exit 1

PY=${PYTHON:-python3}
if ! PY_VERSION=$(
  "$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")'
); then
  echo "Python non trovato: $PY (imposta PYTHON=/percorso/python3.13)" >&2
  exit 2
fi
case "$PY_VERSION" in
  3.11|3.12|3.13) ;;
  *)
    echo "Versione Python non supportata: $PY_VERSION (richiesta 3.11–3.13)" >&2
    exit 2
    ;;
esac
if [ ! -d .venv ]; then
  echo ">> Creo virtualenv..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -r requirements.txt -r requirements-dev.txt
if ! python -c 'import cryptography; print("cryptography " + cryptography.__version__)'; then
  echo "Dipendenza cryptography non disponibile: il vault dei segreti non è sicuro." >&2
  exit 2
fi
# Chiave del vault: cifra i credential dei provider salvati dall'interfaccia.
# Vive nel .env (gitignored, 600), mai nel database accanto al ciphertext.
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
if ! grep -q "^TABULARIUM_VAULT_KEY=" "$ENV_FILE" 2>/dev/null; then
  KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  printf '\n# Cifratura dei credential salvati dalla UI (generata da setup_backend.sh).\nTABULARIUM_VAULT_KEY=%s\n' "$KEY" >> "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  echo ">> Generata TABULARIUM_VAULT_KEY in .env"
fi

# Il wizard cloud (chiave SSH dedicata, pinning host key, tunnel, provisioning
# della GPU) usa i binari di OpenSSH: senza, resta disponibile solo l'inferenza
# locale o gli endpoint HTTPS senza tunnel.
missing=""
for tool in ssh ssh-keygen ssh-keyscan; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
  echo "!! OpenSSH incompleto (mancano:$missing): installa openssh-client per usare le GPU cloud." >&2
fi

echo ">> Backend pronto. Attivalo con: source backend/.venv/bin/activate"
