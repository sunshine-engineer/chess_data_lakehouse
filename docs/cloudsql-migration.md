# Managed Cloud SQL migration

Outline: connect privately, prepare the login/schema, pause writers, upsert data,
verify, and resume the existing application. Execution evidence is recorded
separately from these reusable commands.

For the completed 8–9 November run, see the
[migration handover: steps, SQL, results and recovery](cloudsql-migration-handover.md).

The Python entrypoints are `chess_lakehouse.transfer.managed` for ownership groups,
the requested login/database, and native application DDL; and
`chess_lakehouse.transfer` for source-wins data upsert. All commands run in Docker.
The source database is retained. This does not redirect the existing application
to Cloud SQL or move PGN, Parquet, model or report files.

## Configuration

Copy `.env.cloudsql.example` to the ignored `.env.cloudsql`. Supply your exact
instance connection name, local gcloud configuration directory, Docker network,
database/login names and host UID/GID. An existing authenticated gcloud account
must have access to that instance. The proxy reuses that account through
`--gcloud-auth`; it copies the read-only host configuration into temporary memory
and refreshes credentials there. It does not create a service-account key or
publish a database port on the host. For unattended deployment, use an
appropriately scoped workload identity or ADC instead of this operator session.

Place two existing passwords in the ignored private directory, without quoting
them or adding variable names: `source-password` and `postgres-password`.
`dev-password` is generated once by the preparation script and retained for
resume. Never include real credentials in a committed env file or command line.
Protect these files with host filesystem access controls; POSIX chmod alone does
not establish Windows ACL protection for a Windows-mounted directory.

```bash
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml build proxy migration
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml up -d --wait proxy
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml run --rm --no-deps \
  --entrypoint python migration -m chess_lakehouse.transfer.managed
```

The last command is read-only planning. Verify database versions first. The
native client must be at least as new as the source and no newer than the target;
the provided image uses PostgreSQL 16. A major-version upgrade is a separate
explicitly authorized infrastructure operation, never an automatic side effect
of the Python migration script.

## Schema preparation

```bash
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml run --rm --no-deps \
  --entrypoint python migration -m chess_lakehouse.transfer.managed --run
```

The script creates a new `dev` login with a generated password and creates the
missing application database with the source encoding/locale. Source ownership
and ACL role names become ordinary NOLOGIN groups; source SUPERUSER and other
cluster privileges/passwords are not copied. The new login inherits those groups
so object ownership and permissions can remain intact. Existing role passwords
are not rotated.

Native schema restoration is transactional. The existing default `public` schema
is reused, with source ownership, ACL and comment metadata retained. Existing
populated application schemas must already match the source; the script does not
drop or replace them. A native schema checksum verifies the selected application
namespaces, including views, functions, constraints and indexes. Provider/system
namespaces are outside this boundary.

Preparation state is retained in `cloudsql-schema/schema-preparation.json` with
the source identity, selected instance, requested login/database and schema hash.
Keep its native schema archive and private restore log for recovery.

## Consistent data upsert

First run the normal migration command without write flags to check table
definitions, keys and FK ordering. After that passes, stop only the application
containers writing to the selected source. Leave PostgreSQL and the proxy running.
Source and destination writers must remain paused through final validation.

```bash
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml run --rm --no-deps migration
# After the maintenance window begins:
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml run --rm --no-deps \
  migration --config /app/config/postgres-upsert.toml \
  --workdir /migration/cloudsql-upsert --run --maintenance-window
```

Matching primary keys take source values, including NULLs. Missing keys are
inserted; destination-only rows remain. Logged stages and transactional chunk
markers support resume with the same workdir/config/snapshot. Final verification
checks whole resulting table contents and unchanged schema/database metadata.
The source is rechecked before completion. See [the upsert runbook](postgres-transfer.md)
for identity conflicts, capacity, protected history and rollback limits.

The legacy history table contains a self-referencing parent pointer. When its
full snapshot fits one configured byte/row-bounded chunk, one INSERT can satisfy
those references without disabling immutability triggers or changing parent
links. Larger immutable parent histories are rejected before application-row
writes and need an explicit migration design.

The native source archive, manifest, compact tracker, preservation baseline and
destination `_chess_transfer` ledger are retained. Baseline hashes do not back up
overwritten values; back up an existing destination before merging. Retain stages
through acceptance; they consume additional managed storage.

## Acceptance and service recovery

Require a terminal `status: complete` report, application-schema checksum equality,
per-table content validation, and successful checkpointed resume/idempotence checks. Check application
role access and representative historical/study queries as the requested login.
Record counts without publishing private games, account IDs or credentials.

Restart the exact existing source containers after acceptance if they remain the
active local app. `docker start` avoids an unintended image/config deployment.
New local writes then make Cloud SQL a point-in-time copy. A later synchronization
uses a new job/workdir; it must not reuse a completed snapshot's checkpoints.
Redirecting the app and moving its file volumes is a separate cutover decision.

The read-only acceptance script is `scripts/verify-cloudsql-migration.py`. Mount
`scripts/` at `/checks` in the migration container and run that script; it compares
the destination completion ledger, retained snapshot counts and native schema,
then saves aggregate evidence to `/migration/cloudsql-verification.json`.

Connection and upgrade behavior follow Google's
[Cloud SQL Auth Proxy documentation](https://cloud.google.com/sql/docs/postgres/connect-auth-proxy)
and [major-version upgrade documentation](https://cloud.google.com/sql/docs/postgres/upgrade-major-db-version-inplace).
