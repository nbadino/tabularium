#!/usr/bin/env bash
# ==============================================================================
# Vast.ai onstart script for MonkeyOCRv2 vLLM Inference
# Inserisci questo script nel campo "onstart" del template Vast.ai per avviare
# automaticamente il server GPU ogni volta che l'istanza viene avviata o ripresa.
# ==============================================================================
LOG_FILE="/var/log/vllm_startup.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo ">> [Vast.ai onstart] $(date): Avvio configurazione automatica..."

# Scarica ed esegue lo script di setup
mkdir -p /root/scripts
curl -fsSL https://raw.githubusercontent.com/Yuliang-Liu/MonkeyOCRv2/main/parsing/serve.py -o /dev/null || true

# Avvia lo script di setup vLLM in background
bash /root/setup_cloud_vllm.sh --port 8888 --gpu-mem 0.90 &
echo ">> [Vast.ai onstart] Processo vLLM lanciato in background."
