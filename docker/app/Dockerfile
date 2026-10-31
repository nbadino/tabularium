# Tabularium as a self-contained application image.
#
# One process, one data volume, no external database. The dashboard does not contain
# PyTorch: model runtimes and training are separate environments the app prepares under
# the data volume, or on a remote GPU (program.md, D8).
#
#   docker build -f docker/app/Dockerfile -t tabularium .
#   docker run -p 127.0.0.1:8787:8787 -v tabularium-data:/data tabularium
#
# Bind to loopback and put a TLS proxy in front: until the first admin exists,
# /api/auth/setup is open to whoever reaches it first.
# Digests pinned for reproducible builds; Dependabot (docker) proposes the updates.
FROM node:25-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS frontend
WORKDIR /src/frontend
# Serve the interface under a sub-path (e.g. /tabularium/) by building with
# --build-arg TABULARIUM_BASE_PATH=/tabularium/ and stripping the prefix at the proxy.
ARG TABULARIUM_BASE_PATH=/
ENV TABULARIUM_BASE_PATH=$TABULARIUM_BASE_PATH
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src

# The wheel force-includes these paths (pyproject.toml), so they must be present at build time.
COPY pyproject.toml VERSION README.md LICENSE ./
COPY backend/ ./backend/
COPY conf/ ./conf/
COPY scripts/cloud/ ./scripts/cloud/
COPY scripts/local/ ./scripts/local/
COPY scripts/serve_model.sh ./scripts/serve_model.sh
COPY --from=frontend /src/frontend/dist ./frontend/dist

# Exact versions from the lock: the same image every build, until the lock changes.
RUN pip install -c backend/constraints.txt .

# A dedicated uid keeps the data volume independent from the image user.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin tabularium \
    && mkdir -p /data && chown -R tabularium:tabularium /data /src
USER tabularium
ENV TABULARIUM_ROOT=/data
VOLUME ["/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=3)"

ENTRYPOINT ["tabularium", "--host", "0.0.0.0", "--no-browser"]
