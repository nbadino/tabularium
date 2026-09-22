#!/usr/bin/env bash
# Setup ambiente backend: venv + dipendenze (Python 3.11–3.13).
set -euo pipefail
# `$0` può essere relativo: risolvi la root PRIMA di spostarti, altrimenti
# ogni `dirname "$0"` successivo parte da backend/ e non risolve più.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend" || exit 1

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

# Il motore OCR è una funzione del prodotto (prefill «OCR (CPU)» e riempimento
# delle celle), non un extra: se non si importa lo diciamo qui, con la cura,
# invece di lasciarlo scoprire al primo prefill con «nessun motore disponibile».
if ! python -c 'from rapidocr_onnxruntime import RapidOCR' 2>/dev/null; then
  echo "!! Motore OCR non importabile: il prefill «OCR (CPU)» non sarà disponibile." >&2
  echo "   Verifica l'installazione con: pip install -r requirements.txt" >&2
fi
# OpenCV arriva col motore OCR. La build installata non è headless, quindi su
# Linux pretende le librerie di sistema: senza, `import cv2` fallisce e con lui
# deskew, prospettiva e il motore OCR.
if ! python -c 'import cv2' 2>/dev/null; then
  echo "!! OpenCV non si importa: su Linux installa le librerie di sistema" >&2
  echo "   sudo apt-get install -y libgl1 libglib2.0-0" >&2
  echo "   (su altre distribuzioni il pacchetto equivalente: libGL e glib2)" >&2
fi
# Chiave del vault: cifra i credential dei provider salvati dall'interfaccia.
# Vive nel .env (gitignored, 600), mai nel database accanto al ciphertext.
ENV_FILE="$ROOT/.env"
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
