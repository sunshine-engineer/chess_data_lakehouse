FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ARG KUBECTL_VERSION=v1.36.5
ARG KUBECONFORM_VERSION=v0.8.0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" -o /usr/local/bin/kubectl \
    && curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" -o /tmp/kubectl.sha256 \
    && printf '%s  /usr/local/bin/kubectl\n' "$(cat /tmp/kubectl.sha256)" | sha256sum -c - \
    && chmod 755 /usr/local/bin/kubectl
RUN curl -fsSL "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" -o /tmp/kubeconform-linux-amd64.tar.gz \
    && curl -fsSL "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/CHECKSUMS" -o /tmp/CHECKSUMS \
    && cd /tmp && sha256sum --ignore-missing -c CHECKSUMS \
    && tar -xzf kubeconform-linux-amd64.tar.gz -C /usr/local/bin kubeconform
RUN pip install --no-cache-dir PyYAML==6.0.3 pytest==9.2.0 \
    && groupadd --gid 10001 chess && useradd --uid 10001 --gid 10001 --create-home chess
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CHESS_CONTAINER=1 PYTHONPATH=/workspace/src
USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["python", "scripts/check_repository.py"]
