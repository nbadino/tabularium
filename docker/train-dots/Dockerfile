# flash-attn richiede nvcc: base -devel. Immagine solo ambiente (pesi scaricati
# da HuggingFace al primo run).
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/root/.cache/huggingface MAX_JOBS=8
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates python3-venv libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
RUN python -m venv --system-site-packages /opt/tabularium/venv
ENV PATH=/opt/tabularium/venv/bin:$PATH
RUN python -m pip install --upgrade pip
RUN python -m pip install "ms-swift==4.6.0" "transformers==4.57.1" "accelerate==1.11.0" "qwen_vl_utils==0.0.14"
RUN python -m pip install "flash-attn==2.8.3" --no-build-isolation
RUN python - <<'PY'
import json, pathlib
import torch, transformers, swift, flash_attn
root=pathlib.Path('/opt/tabularium')
(root/'ready.json').write_text(json.dumps({
 'profile':'dots','adapter_id':'dots-ocr','python':str(root/'venv/bin/python'),
 'repo':str(root),'torch':torch.__version__,'transformers':transformers.__version__,
 'ms_swift':getattr(swift,'__version__','unknown'),'flash_attn':flash_attn.__version__}, indent=2))
PY
CMD ["sleep","infinity"]
