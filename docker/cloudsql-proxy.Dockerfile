# gcloud-auth reuses the operator's existing CLI login without creating a key.
FROM gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.27.0@sha256:7d156a5c466e31596afd6c8ed134f19c207a297418392fdad2686524ca4e7a3c AS proxy
FROM gcr.io/google.com/cloudsdktool/google-cloud-cli:slim@sha256:8a50079dfc4b164aa5e83926bef5f9b305b93e80f82d40fabd836da4830dd08e
COPY --from=proxy /cloud-sql-proxy /usr/local/bin/cloud-sql-proxy
COPY docker/ops/start-cloudsql-proxy.sh /usr/local/bin/start-cloudsql-proxy
ENV CLOUDSDK_CONFIG=/tmp/cloudsql-gcloud HOME=/tmp
ENTRYPOINT ["sh", "/usr/local/bin/start-cloudsql-proxy"]
