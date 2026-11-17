# Evaluation image for Xiaomi-OCR-0 runs: the qwen training image (Vast SSH runtime, Python 3.11)
# plus an isolated venv with the exact stack the gold evaluations were scored with, so a rented
# GPU is ready in minutes instead of reinstalling ~3 GB from PyPI on every rental.
FROM ghcr.io/nbadino/tabularium-train-qwen:qwen-v1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
RUN /opt/tabularium/venv/bin/python -m venv /root/eval-env \
 && /root/eval-env/bin/python -m pip install --no-cache-dir \
      "torch==2.14.0" "torchvision==0.29.0" "transformers==5.17.0" pillow accelerate \
 && /root/eval-env/bin/python -m pip install --no-cache-dir --no-deps peft
RUN /root/eval-env/bin/python -c "import torch, transformers, peft; print(torch.__version__, transformers.__version__, peft.__version__)" \
      > /root/eval-env/versions.txt
CMD ["sleep","infinity"]
