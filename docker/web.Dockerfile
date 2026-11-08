FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ENV CHESS_CONTAINER=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 \
    ML_ROOT=/models EVIDENCE_ROOT=/evidence DOCS_ROOT=/app/docs
RUN groupadd --gid 10001 chess && useradd --uid 10001 --gid 10001 --create-home chess
RUN apt-get update && apt-get install -y --no-install-recommends stockfish=15.1-4 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.web.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.web.lock
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps --no-build-isolation .
COPY docs ./docs
COPY tests ./tests
COPY config ./config
USER 10001:10001
CMD ["python", "-m", "chess_lakehouse.serve", "web"]
