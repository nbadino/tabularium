# PP-DocLayoutV3 layout fine-tuning (`backend/app/models/pp_doclayoutv3.py`): the PaddleX
# checkout the recipe's `main.py` runs from, with the PaddleDetection plugin the layout
# module trains through (PaddleX installation guide: `pip install -e ".[base]"`, then
# `paddlex --install PaddleDetection`). Same CUDA base as train-paddle.
FROM paddlepaddle/paddle:3.3.0-gpu-cuda12.6-cudnn9.5
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates python3-venv libgl1 libglib2.0-0 libgomp1 rsync && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 -b release/3.7 https://github.com/PaddlePaddle/PaddleX.git /opt/tabularium/PaddleX
# --system-site-packages: the base image's paddlepaddle-gpu is the CUDA build to keep.
RUN python3 -m venv --system-site-packages /opt/tabularium/venv
ENV PATH=/opt/tabularium/venv/bin:$PATH
RUN python -m pip install --upgrade pip && python -m pip install -e "/opt/tabularium/PaddleX[base]"
# `paddlex --install` and the check below import paddle, whose GPU build loads libcuda.so.1:
# a build has no driver, so those steps alone see the CUDA toolkit's driver stub
# (found on the first CI build, 2026-11-20). The runtime uses the host driver as usual.
RUN STUB="$(find / -path '*stubs*' -name 'libcuda.so' 2>/dev/null | head -1)" && \
    test -n "$STUB" && mkdir -p /opt/cuda-stub && ln -sf "$STUB" /opt/cuda-stub/libcuda.so.1
RUN cd /opt/tabularium/PaddleX && LD_LIBRARY_PATH=/opt/cuda-stub:$LD_LIBRARY_PATH \
    paddlex --install PaddleDetection --use_local_repos
# Found on the first local run (2026-11-20): PaddleDetection still imports `pkg_resources`
# (gone from setuptools 81), and the plugin pins numpy 1.x under a pycocotools wheel built
# for numpy 2, so it is rebuilt against the installed numpy. The last line decodes a mask.
RUN python -m pip install "setuptools<81" cython && \
    python -m pip install --force-reinstall --no-deps --no-binary pycocotools --no-build-isolation pycocotools && \
    LD_LIBRARY_PATH=/opt/cuda-stub:$LD_LIBRARY_PATH python -c "import ppdet, pkg_resources, pycocotools.mask as m; m.decode(m.merge(m.frPyObjects([[0,0,4,0,4,4,0,4]],8,8)))"
RUN python - <<'PY'
import json, pathlib
root=pathlib.Path('/opt/tabularium')
(root/'ready.json').write_text(json.dumps({
 'profile':'layout','adapter_id':'pp-doclayoutv3','python':str(root/'venv/bin/python'),
 'repo':str(root/'PaddleX')}, indent=2))
PY
CMD ["sleep","infinity"]
