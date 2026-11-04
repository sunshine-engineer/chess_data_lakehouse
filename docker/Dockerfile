FROM python:3.12-slim-bookworm
ENV CHESS_CONTAINER=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    SPARK_LOCAL_IP=127.0.0.1 PYSPARK_PYTHON=python
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install '.[test,containers]'
COPY config ./config
COPY tests ./tests
COPY sql ./sql
ENTRYPOINT ["python", "-m", "chess_lakehouse.container_cli"]
