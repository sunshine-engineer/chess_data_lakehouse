FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ENV CHESS_CONTAINER=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    SPARK_LOCAL_IP=127.0.0.1 PYSPARK_PYTHON=python
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 chess && useradd --uid 10001 --gid 10001 --create-home chess
WORKDIR /app
COPY requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps --no-build-isolation . \
    && sha256sum requirements.lock > /app/DEPENDENCIES.sha256
COPY config ./config
COPY tests ./tests
COPY sql ./sql
ARG GIT_REVISION=working-tree
LABEL org.opencontainers.image.revision=$GIT_REVISION
RUN python -c "import hashlib,pathlib; h=hashlib.sha256(); paths=sorted(list(pathlib.Path('src').rglob('*.py'))+list(pathlib.Path('src').rglob('*.sql'))+[pathlib.Path('requirements.lock')]); [(h.update(str(p).encode()),h.update(p.read_bytes())) for p in paths]; pathlib.Path('/app/BUILD_ID').write_text(h.hexdigest())"
USER 10001:10001
ENTRYPOINT ["python", "-m", "chess_lakehouse.optimized.cli"]
