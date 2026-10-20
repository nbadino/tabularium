FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/root/.cache/huggingface
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates python3-venv libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory.git /opt/tabularium/LLaMA-Factory
RUN python -m venv --system-site-packages /opt/tabularium/venv
ENV PATH=/opt/tabularium/venv/bin:$PATH
RUN python -m pip install --upgrade pip
RUN python -m pip install -e /opt/tabularium/LLaMA-Factory
RUN python -m pip install -r /opt/tabularium/LLaMA-Factory/requirements/metrics.txt || true
RUN python -m pip install "transformers==5.8.0"
RUN python - <<'PY'
import json, pathlib
import torch, transformers
root=pathlib.Path('/opt/tabularium')
(root/'ready.json').write_text(json.dumps({
 'profile':'glm','adapter_id':'glm-ocr','python':str(root/'venv/bin/python'),
 'repo':str(root/'LLaMA-Factory'),'torch':torch.__version__,
 'transformers':transformers.__version__}, indent=2))
PY
CMD ["sleep","infinity"]
