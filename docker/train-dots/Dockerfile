# dots.ocr: base torch 2.8 e wheel flash-attn prebuilt per la stessa coppia.
# Il wheel viene scelto in base all'ABI C++ di torch, senza compilare.
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV HF_HOME=/root/.cache/huggingface
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates python3-venv libgl1 libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
RUN python -m venv --system-site-packages /opt/tabularium/venv
ENV PATH=/opt/tabularium/venv/bin:$PATH
RUN python -m pip install --upgrade pip
RUN python -m pip install "ms-swift==4.6.0" "transformers==4.57.1" "accelerate==1.11.0" "qwen_vl_utils==0.0.14"
RUN ABI=$(python -c 'import torch; print("TRUE" if torch._C._GLIBCXX_USE_CXX11_ABI else "FALSE")') \
    && echo "torch cxx11 ABI=$ABI" \
    && python -m pip install "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.8cxx11abi${ABI}-cp311-cp311-linux_x86_64.whl"
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
