#!/bin/sh
# Keep the host gcloud configuration read-only; refreshed tokens live in tmpfs.
set -eu
mkdir -p "$CLOUDSDK_CONFIG"
chmod 700 "$CLOUDSDK_CONFIG"
cp -a /gcloud-seed/. "$CLOUDSDK_CONFIG"/
exec cloud-sql-proxy --gcloud-auth --address=0.0.0.0 --port=5432 \
  --health-check --http-address=0.0.0.0 --http-port=9090 \
  --exit-zero-on-sigterm "$@"
