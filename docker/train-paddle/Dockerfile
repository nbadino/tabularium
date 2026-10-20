FROM paddlepaddle/paddle:3.3.0-gpu-cuda12.6-cudnn9.5
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/root/.cache/huggingface
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates python3-venv libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/PaddlePaddle/PaddleFormers.git /opt/tabularium/PaddleFormers
RUN python3 -m venv --system-site-packages /opt/tabularium/venv
ENV PATH=/opt/tabularium/venv/bin:$PATH
RUN python -m pip install --upgrade pip
RUN python -m pip install -e /opt/tabularium/PaddleFormers
RUN python -m pip install "triton>=3.1"
RUN python - <<'PY'
import json, pathlib
root=pathlib.Path('/opt/tabularium')
(root/'ready.json').write_text(json.dumps({
 'profile':'paddle','adapter_id':'paddleocr-vl','python':str(root/'venv/bin/python'),
 'repo':str(root/'PaddleFormers')}, indent=2))
PY
CMD ["sleep","infinity"]
