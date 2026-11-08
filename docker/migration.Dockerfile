FROM postgres:16
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/migrator \
    && /opt/migrator/bin/pip install --no-cache-dir psycopg[binary]==3.2.13 pytest==9.2.0 \
    && groupadd --gid 10001 chess && useradd --uid 10001 --gid 10001 --create-home chess
ENV PATH=/opt/migrator/bin:$PATH PYTHONPATH=/app/src:/app/tests CHESS_CONTAINER=1 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY src /app/src
COPY tests /app/tests
COPY config /app/config
USER 10001:10001
ENTRYPOINT ["python", "-m", "chess_lakehouse.transfer"]
