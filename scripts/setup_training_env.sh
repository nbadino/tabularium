#!/usr/bin/env bash
# Setup automatico dell'ambiente di TRAINING per MonkeyOCRv2 (ms-swift + torch cu126)
# e download dei pesi del modello di parsing.
#
# Uso:  TABULARIUM_REPO=/percorso/MonkeyOCRv2 ./scripts/setup_training_env.sh
set -euo pipefail

REPO="${TABULARIUM_REPO:-/home/cappannonno/MonkeyOCRv2}"
ENV_NAME="${TABULARIUM_TRAIN_ENV:-monkeyocrv2-train}"
CUDA_INDEX="https://download.pytorch.org/whl/cu126"

if [ ! -d "$REPO/parsing/train/ms-swift" ]; then
  echo ">> checkout del repo in $REPO non trovato. Clono..."
  git clone --depth 1 https://github.com/Yuliang-Liu/MonkeyOCRv2.git "$REPO"
fi

echo ">> creo env conda '$ENV_NAME' (Python 3.11)..."
conda create -y -n "$ENV_NAME" python=3.11
PYBIN="$CONDA_PREFIX/envs/$ENV_NAME/bin/python"
# se CONDA_PREFIX non attivo, deduci la base
if [ ! -x "$PYBIN" ]; then
  BASE="$(conda info --base 2>/dev/null || echo "$HOME/anaconda3")"
  PYBIN="$BASE/envs/$ENV_NAME/bin/python"
fi

echo ">> PyTorch cu126 (download lungo)..."
"$PYBIN" -m pip install torch torchvision torchaudio --index-url "$CUDA_INDEX"

echo ">> dipendenze ms-swift..."
"$PYBIN" -m pip install transformers==4.57.1 accelerate==1.11.0 qwen_vl_utils==0.0.14
# flash-attn opzionale: decommenta se supportato dal tuo hardware
# "$PYBIN" -m pip install flash-attn==2.8.3 --no-build-isolation
"$PYBIN" -m pip install -e "$REPO/parsing/train/ms-swift"

echo ">> download pesi modello (HF)..."
cd "$REPO"
"$PYBIN" download_model.py -n MonkeyOCRv2-B-Parsing

echo ">> verifica CUDA/GPU..."
"$PYBIN" -c "import torch; print('torch', torch.__version__, '| cuda:', torch.cuda.is_available())"

cat <<EOF

Setup completato. Configura il dashboard nel file tabularium/.env:
  TABULARIUM_TRAIN_REPO=$REPO
  TABULARIUM_TRAIN_ENV=$ENV_NAME
EOF