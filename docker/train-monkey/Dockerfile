# Immagine MonkeyOCRv2 che addestra **e** serve: vLLM 0.11 (ramo legacy
# ufficiale) sopra torch 2.8, così la stessa GPU può valutare subito dopo il
# merge. Il fork ms-swift resta quello del repository ufficiale.
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/root/.cache/huggingface
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates python3-venv libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
ARG MONKEY_REPO=https://github.com/Yuliang-Liu/MonkeyOCRv2
ARG MONKEY_REF=6cc0c0bb4864f2541fa6bc3a8f976cffaac8f359
RUN mkdir -p /opt/tabularium/MonkeyOCRv2 \
    && cd /opt/tabularium/MonkeyOCRv2 \
    && git init -q && git remote add origin "${MONKEY_REPO}" \
    && git fetch -q --depth 1 origin "${MONKEY_REF}" \
    && git checkout -q FETCH_HEAD \
    && git submodule update --init --recursive --depth 1
RUN python -m venv --system-site-packages /opt/tabularium/venv
ENV PATH=/opt/tabularium/venv/bin:$PATH
RUN python -m pip install --upgrade pip
RUN python -c 'import torchvision' \
    || python -m pip install "torchvision==0.23.0" --index-url https://download.pytorch.org/whl/cu128
RUN cd /opt/tabularium/MonkeyOCRv2/parsing/train \
    && { [ -f ms-swift/README.md ] || printf '# ms-swift (bundled with MonkeyOCRv2)\n' > ms-swift/README.md; } \
    && mkdir -p ms-swift/swift/llm/dataset/data \
    && { [ -f ms-swift/swift/llm/dataset/data/dataset_info.json ] || printf '[]\n' > ms-swift/swift/llm/dataset/data/dataset_info.json; } \
    && python -m pip install -e ./ms-swift
RUN python -m pip install "transformers==4.57.1" "accelerate==1.11.0" "qwen_vl_utils==0.0.14"
# vLLM 0.11 è il ramo legacy che serve.py accetta per MonkeyOCRv2.
RUN python -m pip install "vllm==0.11.0"
RUN python - <<'PY'
import json, pathlib
import torch, transformers, swift
root=pathlib.Path('/opt/tabularium')
marker={
 'profile':'monkey','adapter_id':'monkeyocrv2-parsing',
 'python':str(root/'venv/bin/python'),
 'repo':str(root/'MonkeyOCRv2/parsing/train'),
 'serve_repo':str(root/'MonkeyOCRv2/parsing'),
 'torch':torch.__version__,'transformers':transformers.__version__,
 'ms_swift':getattr(swift,'__version__','unknown'),
}
(root/'ready.json').write_text(json.dumps(marker, indent=2), encoding='utf-8')
pathlib.Path('/root/tabularium-train').mkdir(parents=True, exist_ok=True)
(pathlib.Path('/root/tabularium-train')/'.train_ready').write_text('image\n', encoding='utf-8')
print('READY', marker)
PY
WORKDIR /opt/tabularium
CMD ["sleep","infinity"]
