#!/usr/bin/env bash
# ==============================================================================
# Vast.ai onstart script for MonkeyOCRv2 vLLM Inference
# Inserisci questo script nel campo "onstart" del template Vast.ai per avviare
# automaticamente il server GPU ogni volta che l'istanza viene avviata o ripresa.
# ==============================================================================
LOG_FILE="/var/log/vllm_startup.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo ">> [Vast.ai onstart] $(date): Avvio configurazione automatica..."
: "${MONKEYOCR_REF:?MONKEYOCR_REF obbligatorio: impostare un commit SHA o un tag verificato nelle env dellistanza}"
: "${TABULARIUM_REF:?TABULARIUM_REF obbligatorio: impostare il commit SHA o tag del repository Tabularium}"

TABULARIUM_RAW_BASE="${TABULARIUM_RAW_BASE:-https://raw.githubusercontent.com/nbadino/tabularium}"

# Scarica ed esegue lo script di setup. Il ref viene passato esplicitamente:
# il server non deve partire da codice `main` non riproducibile.
mkdir -p /root/scripts
curl --fail --silent --show-error --location \
  "${TABULARIUM_RAW_BASE}/${TABULARIUM_REF}/scripts/cloud/setup_cloud_vllm.sh" \
  --output /root/setup_cloud_vllm.sh
chmod 700 /root/setup_cloud_vllm.sh

# Avvia lo script di setup vLLM in background
bash /root/setup_cloud_vllm.sh --port 8888 --gpu-mem 0.90 --ref "$MONKEYOCR_REF" &
echo ">> [Vast.ai onstart] Processo vLLM lanciato in background."
