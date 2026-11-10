# Cloud SQL migration: steps, SQL and handover

Recorded migration: **8–9 November 2026**. Source implementation: commit
`8c72117` (`CXOED/cloudsql-managed-migration`). This file was assembled from the
implementation, retained migration artifacts and execution evidence after the run.

Outline:

1. Scope, settings and accepted results.
2. Ordered Docker commands and SQL used.
3. How chunked upsert, recovery and verification work.
4. Credentials, retained artifacts, limitations and rollback.
5. SQL reference extracted from the implementation.

## 1. Scope and accepted results

The migration copied the application's PostgreSQL objects and table data from
local Docker PostgreSQL into the selected GCP Cloud SQL instance. It created
the `dev` login, prepared compatible schemas, then used **source-wins upsert**.
The source database was retained. PGN/Parquet files and other file volumes were
outside this database migration.

| Setting | Value used |
|---|---|
| Source | Docker container `chess-lakehouse-postgres-1`, port 5432 |
| Source database / administrator | `chess` / `chess` |
| Target instance / region | `postgres-test` / `asia-south2` |
| Exact project and instance connection name | Local ignored `.env.cloudsql`; deliberately omitted here |
| Target database / migration login | `chess` / `dev` |
| Target administrator | `postgres`, used for initial role/database setup |
| PostgreSQL versions | Source 16.15; target upgraded from 15 to 16, verified as 16.15 |
| Database encoding / locale | UTF8 / `en_US.utf8` for both collation and ctype |
| Source database size at discovery | 1,112,308,759 bytes; not the transfer size |
| Source extension inventory | `plpgsql` only |
| Transfer configuration | [config/postgres-upsert.toml](../config/postgres-upsert.toml) |
| Chunk limits | 50,000 rows or 16 MiB; a single row may exceed 16 MiB up to the 64 MiB row limit |
| Parallel dump workers / local disk reserve | 2 / 1 GiB minimum; the reserve is not a full capacity estimate |
| Migration container limits | 2 CPUs, 1 GiB memory |
| Application ownership/permission groups | `chess`, `chess_loader`, `chess_reader`, `chess_study` |

**Conflict behavior:** an existing primary key takes the source's mutable values,
including NULLs. Missing keys are inserted. Destination-only rows remain. Existing
immutable published history must already agree; conflicting changes are rejected.
Different primary keys sharing an alternate unique identity require reconciliation.
The script does not guess identity mappings, drop tables, or replace an incompatible
schema. Missing schema objects are handled separately by schema preparation.

The accepted [aggregate report](../reports/cloudsql-migration.json) records:

| Acceptance check | Recorded result |
|---|---|
| Application schemas / physical tables | 20 / 121 |
| Source rows / resulting rows verified | 3,586,769 / 3,586,769 |
| Destination-only rows in this live run | 0; preservation was exercised in Docker regression tests |
| Committed ledger units | 478; includes table setup, chunks and other checkpoints, not 478 tables |
| Application constraints | All validated |
| Login | `dev`, not a superuser |
| Interruption recovery | Real SIGINT and successful resume using the original snapshot |
| Native application schema SHA256 | `a6b5d508370731ccbdf13e5163c7c29ae01d152ba83187140c749bbbb513fa87` |
| Data migration completed | `2026-11-09T04:31:34.813726+00:00` |
| Separate acceptance passed | `2026-11-09T04:33:45.274808+00:00` |
| Job ID | `9d17113e-e6a4-4c27-a9a9-d3a2e8e02573` |

Timestamps above are UTC; acceptance was **10:03:45 IST on 9 November**.
Representative counts were:

| Relation | Rows |
|---|---:|
| `warehouse_python.fact_game` | 121,332 |
| `warehouse_python_mp.fact_game` | 403,928 |
| `warehouse_spark.fact_game` | 121,332 |
| `study.accounts` | 2 |
| `study.game_revisions` | 25,052 |
| `study.library_snapshots` | 8 |
| `study.snapshot_games` | 75,305 |
| `study.saved_studies` | 1 |

The warehouse parent counts include their partition leaves. Whole-table checks
read physical tables with `ONLY` to avoid counting partition data twice.

## 2. Execution steps and commands

These are the commands used, with repeated flags normalized and sensitive
identifiers replaced by variables. They are an execution record and reuse guide,
**not a batch to rerun against the completed migration**. Read-only inspections,
write commands and the deliberate interruption are identified separately.
Run from the repository root. Python, PostgreSQL clients and gcloud execute in
Docker; host shell commands handle files and container orchestration.

### Step 1 — Inventory the source and check target compatibility

The source queries below were run using `psql` inside its PostgreSQL container:

```bash
docker exec -i chess-lakehouse-postgres-1 psql -U chess -d chess
```

```sql
SELECT current_database(), version(), pg_database_size(current_database()),
       datcollate, datctype
FROM pg_database WHERE datname = current_database();

SELECT n.nspname, count(*)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r','p','v','m')
  AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
GROUP BY n.nspname ORDER BY n.nspname;

-- Must return no physical application tables lacking a primary key.
SELECT n.nspname, c.relname
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname NOT LIKE 'pg_%'
  AND n.nspname <> 'information_schema'
  AND NOT EXISTS (
    SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary
  )
ORDER BY 1, 2;

SELECT extname, extversion FROM pg_extension ORDER BY extname;
SELECT 'legacy_imports', count(*), count(parent_id) FROM study.imports;
SELECT 'legacy_games', count(*) FROM study.games;
SELECT 'materialized_views', count(*) FROM pg_class WHERE relkind = 'm';
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin
FROM pg_roles
WHERE rolname IN ('chess','chess_reader','chess_loader','chess_study')
ORDER BY rolname;
```

All 121 physical tables had primary keys. Legacy `study.imports` had two rows,
one with a parent reference; legacy `study.games` had 166 rows. There were no
materialized views in this source snapshot. Automated catalog checks also inspect
column types, generated/identity columns, partitions, foreign keys, unique keys,
triggers, rules and sequences; see the SQL reference below.

The target was inspected with the following gcloud arguments inside the official
SDK image. `$PROJECT_ID` refers to the selected project in private configuration.

```bash
gcloud sql instances describe postgres-test --project "$PROJECT_ID" \
  --format='json(name,connectionName,databaseVersion,state,region,ipAddresses,settings.ipConfiguration,settings.dataDiskSizeGb,settings.tier)'
gcloud sql instances describe postgres-test --project "$PROJECT_ID" \
  --format='json(databaseVersion,upgradableDatabaseVersions,settings.backupConfiguration)'
```

For these commands, the Docker wrapper used the existing authenticated gcloud
configuration as a read-only seed. This reusable form accepts gcloud arguments:

```bash
# Set PROJECT_ID from your private configuration, and GCLOUD_CONFIG_DIR to the
# existing authenticated local gcloud directory. Neither is a password.
gcloud_in_docker() {
  docker run --rm \
    -v "${GCLOUD_CONFIG_DIR}:/seed:ro" \
    --tmpfs /tmp:rw,noexec,nosuid,size=134217728 \
    -e CLOUDSDK_CONFIG=/tmp/cloudsql-gcloud \
    gcr.io/google.com/cloudsdktool/google-cloud-cli:slim \
    sh -c 'mkdir -p "$CLOUDSDK_CONFIG" && cp -a /seed/. "$CLOUDSDK_CONFIG"/ && exec gcloud "$@"' \
    sh "$@"
}
```

### Step 2 — Perform the approved PostgreSQL 15 → 16 upgrade

The user explicitly approved this instance change. It was not an automatic
migration-script action. The actual write and follow-up inspection were:

```bash
gcloud_in_docker sql instances patch postgres-test --project "$PROJECT_ID" \
  --database-version=POSTGRES_16 --quiet --async \
  --format='json(name,status,operationType,targetId)'
gcloud_in_docker sql operations describe \
  fd4426ab-6166-40b6-84b6-a53c00000049 --project "$PROJECT_ID" \
  --format='json(name,status,error,endTime)'
```

The operation reached `DONE` at `2026-11-08T18:06:27.475Z`. Instance inspection
confirmed `POSTGRES_16` and `RUNNABLE`; SQL later confirmed server version 16.15.
The source and the PostgreSQL 16 dump/restore clients were then compatible with
the target. Do not replay the upgrade as a routine synchronization step.

### Step 3 — Configure private files and start the proxy

The ignored `.env.cloudsql` was based on
[.env.cloudsql.example](../.env.cloudsql.example). Existing passwords were supplied
through mounted files, never literal command arguments. `dev-password` was
generated by the preparation script. No service-account key was created.

```bash
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml build proxy migration
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml up -d --wait proxy
```

The [Compose configuration](../compose.cloudsql.yaml) joins the source's existing
Docker network. The proxy has no published host database port. `sslmode=disable`
applies to the local container-to-proxy connection; the Cloud SQL Auth Proxy
handles the authenticated encrypted connection to Cloud SQL. This is a local
operator setup; unattended deployment needs an appropriate managed identity.

Read-only target discovery SQL included:

```sql
SELECT current_user, current_database(), version();
SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname;
```

### Step 4 — Plan and prepare roles, database and application objects

```bash
# Read-only plan; the target dev password need not exist yet.
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml \
  run --rm --no-deps --entrypoint python migration \
  -m chess_lakehouse.transfer.managed

# Write: create missing roles/database, restore native schema and verify it.
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml \
  run --rm --no-deps --entrypoint python migration \
  -m chess_lakehouse.transfer.managed --run
```

[managed.py](../src/chess_lakehouse/transfer/managed.py) creates ordinary ownership
groups and a non-superuser login. It never copies source role passwords or
superuser attributes, and never resets an existing login's password. The
following is the role/database SQL shape, with a deliberate password placeholder;
the script quotes identifiers and the generated secret using psycopg:

```sql
-- Repeat CREATE ROLE for each missing application ownership/permission group.
CREATE ROLE chess NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS;
CREATE ROLE dev LOGIN PASSWORD '<generated secret from private file>'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT chess, chess_loader, chess_reader, chess_study TO dev, postgres;

-- CREATE DATABASE runs outside a transaction.
CREATE DATABASE chess OWNER chess TEMPLATE template0
  ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8';
```

Native DDL was restored transactionally, preserving owners, grants, functions,
views, indexes and constraints. The internal client commands have this shape;
endpoint passwords are provided in isolated process environments:

```bash
# --schema arguments expand to each of the 20 selected application namespaces.
pg_dump --format=custom --schema-only --no-password --no-tablespaces \
  --file=/migration/cloudsql-schema/schema.dump --schema='<quoted application schema>'
pg_restore --list /migration/cloudsql-schema/schema.dump
pg_restore --exit-on-error --single-transaction --no-tablespaces \
  --use-list=/migration/cloudsql-schema/schema-selection.list \
  --dbname=chess /migration/cloudsql-schema/schema.dump
```

These internal command templates run in the migration container; they omit the
programmatically expanded schema arguments and endpoint environment. Use the
Python entrypoint for an actual preparation.

A fresh PostgreSQL database already contains `public`. The selection list omits
only its CREATE SCHEMA entry; its ACL/comment entries remain. Source ownership is
then applied with:

```sql
ALTER SCHEMA public OWNER TO pg_database_owner;
```

Preparation compares native application schema hashes before accepting the
destination. An existing populated but incompatible application schema stops
the process instead of being replaced.

### Step 5 — Validate the upsert plan, then pause application writers

```bash
# Read-only data migration plan; default command selects postgres-upsert.toml.
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml \
  run --rm --no-deps migration

# Begin maintenance: stop the specific source writer/UI containers only.
docker stop --time 40 chess-lakehouse-archive-worker-1 \
  chess-lakehouse-study-api-1 chess-lakehouse-web-1
```

`--time` was the flag used; Docker's current equivalent is `--timeout 40`.
PostgreSQL and the proxy stayed running. Destination application writers must
also be paused. The plan confirmed 121 compatible tables and `source-wins` mode.

### Step 6 — Export a consistent snapshot and load bounded chunks

```bash
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml \
  run --rm --no-deps migration \
  --config /app/config/postgres-upsert.toml \
  --workdir /migration/cloudsql-upsert --run --maintenance-window
```

The source snapshot session starts with:

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT pg_export_snapshot();
```

The exported snapshot is shared with the parallel native dump while its session
remains open. The internal command is:

```bash
pg_dump --format=directory --file=/migration/cloudsql-upsert/archive.partial \
  --jobs=2 --compress=6 --snapshot='<exported snapshot ID>' --no-password \
  --exclude-schema=_chess_transfer --no-tablespaces --encoding=UTF8
```

The complete archive is renamed to `archive/`. The native archive totaled
202,438,159 bytes. A manifest records file hashes, source identity, catalog,
sequence state and per-table row fingerprints. It is verified before resume.

The load stages source chunks in logged destination tables, verifies those
contents, records a baseline including destination-only rows, then upserts in
foreign-key order. Each chunk and its ledger marker commit in one transaction.
See section 3 for the transaction and verification SQL.

### Step 7 — Prove interruption and resume against the same snapshot

A deliberate SIGINT interrupted the running migration container:

```bash
# Historical container ID: do not use this name for another run.
docker kill --signal=SIGINT chess-cloudsql-transfer-migration-run-0b05c2ee8bff
```

It exited 130, retaining its original archive, staging tables, baseline and
commit ledger. The same Step 6 command then resumed with the same workdir and
configuration. Committed chunks were skipped. The terminal ledger recorded 478
units. The intermediate JSON progress counter was not the authoritative unit
count; the database ledger is authoritative.

Verification was also optimized to aggregate row hashes on PostgreSQL instead
of fetching every row hash over the network. The arithmetic produces the same
fingerprints as the original implementation, so the original snapshot and
checkpoints remained valid. This was a correctness/recovery exercise, not a
throughput benchmark.

### Step 8 — Run independent read-only acceptance

```bash
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml \
  run --rm --no-deps \
  -v /mnt/d/projects/chess_lakehouse/scripts:/checks:ro \
  --entrypoint python migration /checks/verify-cloudsql-migration.py
cp data/transfers/cloudsql/cloudsql-verification.json reports/cloudsql-migration.json
```

The [verifier](../scripts/verify-cloudsql-migration.py) authenticates as `dev`,
checks native application DDL, terminal tracker/ledger agreement, representative
counts and validated constraints. **Whole-table row hashing occurs in migration
completion, not again in this separate verifier.** Its SQL is included below.

### Step 9 — Restart the exact original local services

```bash
docker start chess-lakehouse-study-api-1 chess-lakehouse-archive-worker-1 \
  chess-lakehouse-web-1
docker inspect --format \
  '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' \
  chess-lakehouse-study-api-1 chess-lakehouse-archive-worker-1 \
  chess-lakehouse-web-1 chess-cloudsql-transfer-proxy-1
```

The API, web and proxy were healthy. The original archive worker was running
and has no health probe. Local repertoire work resumed. `docker start` retained
the original container images/configuration.

**There was no application cutover:** the local application still uses Docker
PostgreSQL. Cloud SQL is the accepted snapshot, not continuous replication.
Source writes after the maintenance window are not automatically synchronized.
These are end-of-migration observations, not a live health check at document read time.

### Step 10 — Regression and packaging checks

The migration work also ran these container checks:

```bash
docker compose -f compose.migration-qa.yaml run --rm tests
docker run --rm -v "$PWD:/workspace" chess-lakehouse-ops:local --write-api
docker compose -f compose.migration-qa.yaml down --volumes --remove-orphans
docker compose --env-file .env.cloudsql -f compose.cloudsql.yaml build migration
docker run --rm --entrypoint python chess-lakehouse-migrator:local -m pip check
docker run --rm --entrypoint python chess-lakehouse-migrator:local \
  -m chess_lakehouse.transfer.managed --help
```

Results: **40 database tests passed in 156.79 seconds**, dependencies passed,
323 public functions were documented and all eight Kustomize targets passed
strict schema validation. The QA teardown removed only the isolated synthetic
test services/volumes. It did not remove the real source database or cloud data.
These checks do not establish full-scale throughput or a cloud application deployment.

## 3. Upsert, checkpoint and verification SQL

These are explanatory templates, not a standalone SQL script. Actual identifiers
are composed with psycopg; `%s` values are bound, never interpolated manually.
The appendix contains the implementation's detailed catalog and execution SQL.

### Session and durable checkpoint ledger

Connections use timezone UTC, ISO dates, `extra_float_digits=3` and hex bytea
output so typed row serialization is stable. The target lock timeout is 30 seconds.
A local file lock and destination advisory lock prevent overlapping migrations.

```sql
SELECT pg_try_advisory_lock(hashtext('chess-database-transfer'));
SELECT set_config('lock_timeout', %s, false);
CREATE SCHEMA _chess_transfer;
REVOKE ALL ON SCHEMA _chess_transfer FROM PUBLIC;
CREATE TABLE _chess_transfer.jobs (
  id UUID PRIMARY KEY, config_hash TEXT NOT NULL, manifest_hash TEXT NOT NULL,
  status TEXT NOT NULL, report JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE _chess_transfer.units (
  job_id UUID NOT NULL REFERENCES _chess_transfer.jobs,
  unit_id TEXT NOT NULL, checksum TEXT NOT NULL,
  rows BIGINT NOT NULL DEFAULT 0,
  committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (job_id, unit_id)
);
```

Schema creation only runs when missing. An existing ledger must match the job,
configuration and manifest. New jobs are refused while another job is unfinished.

### Stage, merge and defer nullable foreign keys

```sql
-- Stage name includes job UUID and source relation OID.
CREATE TABLE _chess_transfer.stage_<job>_<oid>
  (LIKE <schema.table> INCLUDING GENERATED);
ALTER TABLE _chess_transfer.stage_<job>_<oid> ADD PRIMARY KEY (<key_columns>);

BEGIN;
COPY _chess_transfer.stage_<job>_<oid> (<non_generated_columns>) FROM STDIN;
-- COPY data is streamed separately, not pasted into SQL or public logs.
INSERT INTO _chess_transfer.units (job_id, unit_id, checksum, rows)
VALUES (%s, %s, %s, %s);
COMMIT;

BEGIN;
CREATE TEMP TABLE upsert_chunk
  (LIKE <schema.table> INCLUDING GENERATED) ON COMMIT DROP;
COPY upsert_chunk (<non_generated_columns>) FROM STDIN;
INSERT INTO <schema.table> AS t (<non_generated_columns>)
  OVERRIDING SYSTEM VALUE
SELECT <source_columns_or_NULL_for_postponed_foreign_keys>
FROM upsert_chunk WHERE true
ON CONFLICT (<primary_key_columns>)
DO UPDATE SET <mutable_column> = EXCLUDED.<mutable_column>
WHERE t.<mutable_column> IS DISTINCT FROM EXCLUDED.<mutable_column>;
INSERT INTO _chess_transfer.units (job_id, unit_id, checksum, rows)
VALUES (%s, %s, %s, %s);
COMMIT;

-- Later, inside another chunk transaction with its own marker:
UPDATE <schema.table> AS t
SET <postponed_fk> = s.<postponed_fk>
FROM upsert_chunk AS s WHERE <primary_key_equality>;
```

The implementation expands assignments for every mutable non-key column and
uses `DO NOTHING` for key-only tables. Generated columns are computed by
PostgreSQL; source identity values are retained using `OVERRIDING SYSTEM VALUE`.
Postponed nullable foreign keys are patched after referenced rows exist; existing
destination values are retained until that patch. Unsupported non-nullable
cycles fail preflight. Triggers and constraints remain enabled.

Legacy immutable self-referencing history fit one bounded chunk; one INSERT
satisfied the references without bypassing its published-history guard. A larger
immutable self-reference requiring multiple chunks is explicitly rejected.

### Preserve destination-only rows and advance sequences

Before changing application rows, the baseline fingerprints source rows plus
target rows whose primary keys do not appear in the stage:

```sql
SELECT sha256(convert_to(row_to_json(t)::text, 'UTF8'))
FROM ONLY <schema.table> AS t
WHERE NOT EXISTS (
  SELECT 1 FROM <stage_table> AS s WHERE <primary_key_equality>
);

SELECT last_value FROM <owned_sequence>;
SELECT max(<identity_column>) FROM <owning_table>;
SELECT setval(%s::regclass, %s, true);
```

For supported owned identity/serial sequences, the chosen value is the maximum
of current destination sequence value, source sequence value and retained IDs.
It never moves backward. PostgreSQL sequence advancement is not transactional;
an interruption may leave harmless gaps. Sequence advancement is handled
separately from the native DDL checksum, which does not contain current values.

### Whole-table content checks

For each physical table, the pipeline compares the **exact row count** and the
**sum of individual SHA256 row hashes modulo 2²⁵⁶**. This is an order-independent
fingerprint, not the SHA256 of an entire dump file. Duplicate rows contribute
multiple times. The source snapshot, durable stage and merged target must agree
with their expected contents.

```sql
SELECT sha256(convert_to(row_to_json(t)::text, 'UTF8'))
FROM ONLY <schema.table> AS t;
```

The server-side aggregate splits each 32-byte hash into four signed 64-bit words,
sums them using PostgreSQL's exact numeric arithmetic, and counts sign bits.
Python reconstructs the unsigned 256-bit modular sum. Only the count and eight
aggregate values cross the network, rather than one digest per row. Exact query
construction is in the `inventory.py` SQL reference. This fingerprint is a strong
consistency check; it is not a byte-for-byte archive comparison.

Completion also rechecks source contents, target schema and database metadata,
then commits the completion record:

```sql
UPDATE _chess_transfer.jobs SET status = 'complete', report = %s WHERE id = %s;
```

### Operator monitoring and acceptance queries

These read-only query forms allow inspection using an authenticated target
connection inside the migration container. Add `WHERE job_id = %s` when inspecting
a specific job on a destination retaining multiple migrations.

```sql
SELECT id, status, report FROM _chess_transfer.jobs ORDER BY id;
SELECT split_part(unit_id, ':', 1) AS phase, count(*) AS units, sum(rows) AS rows
FROM _chess_transfer.units GROUP BY 1 ORDER BY 1;

-- Acceptance queries used by the verifier:
SET TRANSACTION READ ONLY;
SELECT status, report FROM _chess_transfer.jobs WHERE id = %s;
SELECT rolsuper FROM pg_roles WHERE rolname = current_user;
SELECT current_user, current_database();
SELECT count(*) FROM <representative_schema.table>;
SELECT count(*)
FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
WHERE n.nspname = ANY(%s) AND NOT c.convalidated;
```

Do not add `sum(rows)` across all phases to estimate migrated rows: the same
rows appear in staging, merge and possibly FK patch phases. Use the terminal
report for verified row totals.

## 4. Credentials, retained artifacts and recovery

### Credential handling

| Local ignored path | Purpose |
|---|---|
| `.env.cloudsql` | Instance connection name, source network and mounted directory settings |
| `.env` | Existing local application configuration; retained unchanged |
| `data/secrets/cloudsql/postgres-password` | Target administrator password |
| `data/secrets/cloudsql/source-password` | Source administrator password |
| `data/secrets/cloudsql/dev-password` | Generated target login password; retain for resume |
| Existing local gcloud configuration directory | Operator authentication, mounted read-only and copied into container tmpfs |

These files must stay out of Git, screenshots and shared reports. Ignoring a
file is not access control or encryption. Protect the Windows host directory
with appropriate ACLs; chmod alone was not effective on this `/mnt/d` mount.
The generated dev password is never printed here. The login inherits application
ownership groups for migration; use separate narrower runtime logins for a
future application deployment.

### Retained evidence and native SQL

Paths below are local ignored artifacts unless stated otherwise. They will not
be available from a fresh Git clone. Archives contain real database data and
must be treated as private. Preserve them together until retention is agreed.

| Artifact under `data/transfers/cloudsql/` | Purpose |
|---|---|
| `cloudsql-schema/schema-preparation.json` | Source/target identity, role/schema selection and schema hash |
| `cloudsql-schema/schema.dump` | Native custom schema-only archive |
| `cloudsql-schema/schema-selection.list` | Exact native restore selection, excluding only CREATE public |
| `cloudsql-schema/schema-restore.log` | Private restore log; empty on successful recorded restore |
| `cloudsql-schema/schema-restored.sql` | Offline SQL rendering of that archive/selection, added for this handover |
| `cloudsql-upsert/archive/` | Completed native directory-format source data snapshot |
| `cloudsql-upsert/schema.sql` | Full native source schema from the data snapshot |
| `cloudsql-upsert/manifest.json` | Catalog, file hashes, row fingerprints and sequence states |
| `cloudsql-upsert/job.json` | Recovery seed binding job and configuration |
| `cloudsql-upsert/tracker.json` | Compact progress file; terminal complete report |
| `cloudsql-upsert/tracker-before-checksum-aggregation.json` | Retained interruption/progress evidence |
| `cloudsql-upsert/upsert-baseline.json` | Expected merged fingerprints and target metadata; not a row backup |
| `cloudsql-upsert/dump.log` | Private native dump log |
| `cloudsql-verification.json` | Read-only acceptance output |
| Destination `_chess_transfer` schema | Authoritative jobs/units ledger and retained source stage tables |

The full DDL is available locally in
[schema-restored.sql](../data/transfers/cloudsql/cloudsql-schema/schema-restored.sql)
and [snapshot schema.sql](../data/transfers/cloudsql/cloudsql-upsert/schema.sql).
These are private local links, not committed SQL exports. Schema files can
contain sensitive object metadata; review them before sharing.

The restored SQL rendering was generated **offline**, with no database connection:

```bash
docker run --rm --network none --user 1000:1000 \
  -v "$PWD/data/transfers/cloudsql:/migration" \
  --entrypoint pg_restore chess-lakehouse-migrator:local \
  --exit-on-error --single-transaction --no-tablespaces \
  --use-list=/migration/cloudsql-schema/schema-selection.list \
  --file=/migration/cloudsql-schema/schema-restored.sql \
  /migration/cloudsql-schema/schema.dump
```

It renders the selected native restore DDL, not the separate role/database
creation statements or later public-owner adjustment. There was no full SQL
wire/audit log. This handover preserves the commands, implementation templates
and selected native DDL, not a claim that every conditional query was executed.
Private row payloads are retained only in the migration artifacts.

### Resume, repeat synchronization and rollback

1. **Interrupted job:** keep both databases' writers paused. Retain the original
   archive, manifest, baseline, job files, destination stages and ledger. Rerun
   Step 6 with the same paths/configuration. Checksums and ledger determine
   which chunks committed. Do not delete markers, replace the source archive,
   or manually reset a tracker to force progress.
2. **Completed job:** the recorded job is already complete. It is not a live
   synchronization service. For a later snapshot, establish another maintenance
   window, check compatibility, back up a populated target, and choose a **new
   workdir** so a new manifest/job is created. Keep the old evidence. Use the
   [upsert runbook](postgres-transfer.md) for supported new-job behavior.
3. **Rollback:** the source was retained and the local app was not redirected,
   so local service recovery used `docker start`. Upsert can overwrite existing
   destination values; undoing those requires a destination backup/PITR or
   recorded preimages. The baseline fingerprint cannot reconstruct old rows.
   A destination rollback/Cloud SQL restore rehearsal was not part of this run.
4. **Cleanup:** stages, native archives and checkpoints consume disk. There is
   no automatic post-acceptance cleanup. Do not remove recovery evidence during
   an unfinished job. Future retention/cleanup should name the accepted job,
   preserve required backups and avoid other jobs' stage tables.

### Issues resolved and remaining boundaries

| Issue encountered | Resolution / boundary |
|---|---|
| PostgreSQL 15 target vs 16 source | Explicitly approved target upgrade before native migration |
| Existing `public` schema in a new database | Skip its CREATE entry, retain ACL/comment DDL and apply its owner |
| Bootstrap ownership absent from shared-dependency lookup | Collect actual schema/table/function/type owners as well as ACL role dependencies |
| Managed PostgreSQL cannot copy source superuser privileges | Ordinary ownership groups and a non-superuser dev login |
| Immutable legacy history with a parent pointer | One bounded INSERT; guard triggers stay enabled |
| Per-row hash retrieval slow over the proxy | Equivalent server-side aggregate; resume checked against original fingerprints |
| Approval-review timeouts during earlier work | Rejected attempts did not execute; allowed retries were made after access was restored |

This live dataset was about 1.1 GB in PostgreSQL. It does not demonstrate a
30 GB compressed monthly archive backfill or one-to-five-year processing capacity.
Object coverage is the selected application schemas and their compatible native
objects, not a clone of provider/system catalogs, cluster settings or privileged
roles. Schema preparation currently requires review for extensions beyond plpgsql.
Upsert supports the explicitly checked table/constraint shapes; it is not a
general arbitrary-database conflict resolver. No materialized views existed in
this source snapshot. Future cloud application cutover, runtime least privilege,
secret management, monitoring, backup policy, file/object storage transfer and
rollback rehearsal remain separate deployment work.

Related references: [reusable Cloud SQL guide](cloudsql-migration.md),
[PostgreSQL transfer runbook](postgres-transfer.md),
[validation evidence](portability-validation.md),
[managed preparation implementation](../src/chess_lakehouse/transfer/managed.py),
[upsert implementation](../src/chess_lakehouse/transfer/upsert.py).

## 5. Detailed SQL reference from the implementation

The following reference is extracted from SQL string templates in the migration
modules and acceptance script at the recorded implementation. It includes
catalog checks and conditional paths, including queries that can return no rows
for this database. It is **not a sequential audit log or runnable batch**.
`%s` denotes a bound value. `{}` and named braces denote composed identifiers or
SQL fragments; they are filled safely by psycopg or internal constants. Dynamic
f-strings are shown in Python form so their placeholders remain visible.
Construction fragments and branch logic remain in the linked source files.
Native object DDL is retained separately in the local SQL artifacts above.

<!-- SQL_REFERENCE_START -->

### `managed.py` — 14 templates

<details>
<summary>Expand SQL reference</summary>

Query template at [source line 23](../src/chess_lakehouse/transfer/managed.py#L23):

```sql
SELECT nspname FROM pg_namespace
        WHERE left(nspname,3)<>'pg_' AND nspname NOT IN ('information_schema',%s) ORDER BY nspname
```

Query template at [source line 46](../src/chess_lakehouse/transfer/managed.py#L46):

```sql
SHOW server_version_num
```

Query template at [source line 49](../src/chess_lakehouse/transfer/managed.py#L49):

```sql
SELECT extname FROM pg_extension WHERE extname<>'plpgsql'
```

Query template at [source line 52](../src/chess_lakehouse/transfer/managed.py#L52):

```sql
SELECT oid::text,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'
```

Query template at [source line 53](../src/chess_lakehouse/transfer/managed.py#L53):

```sql
SELECT rolname FROM pg_roles
```

Query template at [source line 54](../src/chess_lakehouse/transfer/managed.py#L54):

```sql
SELECT 1 FROM pg_database WHERE datname=%s
```

Query template at [source line 57](../src/chess_lakehouse/transfer/managed.py#L57):

```sql
WITH app AS (
            SELECT oid FROM pg_namespace WHERE nspname=ANY(%s))
            SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE oid IN (SELECT oid FROM app)
            UNION SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relnamespace IN (SELECT oid FROM app)
            UNION SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE pronamespace IN (SELECT oid FROM app)
            UNION SELECT pg_get_userbyid(typowner) FROM pg_type WHERE typnamespace IN (SELECT oid FROM app)
```

Query template at [source line 94](../src/chess_lakehouse/transfer/managed.py#L94):

```sql
SELECT current_user
```

Query template at [source line 97](../src/chess_lakehouse/transfer/managed.py#L97):

```sql
CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
```

Query template at [source line 99](../src/chess_lakehouse/transfer/managed.py#L99):

```sql
CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
```

Query template at [source line 105](../src/chess_lakehouse/transfer/managed.py#L105):

```sql
SELECT rolsuper FROM pg_roles WHERE rolname=%s
```

Query template at [source line 107](../src/chess_lakehouse/transfer/managed.py#L107):

```sql
GRANT {} TO {},{}
```

Query template at [source line 109](../src/chess_lakehouse/transfer/managed.py#L109):

```sql
CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING {} LC_COLLATE {} LC_CTYPE {}
```

Query template at [source line 139](../src/chess_lakehouse/transfer/managed.py#L139):

```sql
ALTER SCHEMA public OWNER TO {}
```

</details>

### `inventory.py` — 15 templates

<details>
<summary>Expand SQL reference</summary>

Query template at [source line 12](../src/chess_lakehouse/transfer/inventory.py#L12):

```sql
SELECT current_database(),oid::text,pg_encoding_to_char(encoding),
        datcollate,datctype,pg_get_userbyid(datdba),obj_description(oid,'pg_database')
        FROM pg_database WHERE datname=current_database()
```

Query template at [source line 16](../src/chess_lakehouse/transfer/inventory.py#L16):

```sql
SHOW server_version_num
```

Query template at [source line 19](../src/chess_lakehouse/transfer/inventory.py#L19):

```sql
SELECT system_identifier::text FROM pg_control_system()
```

Query template at [source line 30](../src/chess_lakehouse/transfer/inventory.py#L30):

```sql
SELECT c.oid::bigint,n.nspname,c.relname,c.relkind,c.relispopulated,
        ARRAY(SELECT a.attname FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0
              AND NOT a.attisdropped AND a.attgenerated='' ORDER BY a.attnum),c.relpersistence
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','m') AND left(n.nspname,3)<>'pg_'
        AND n.nspname NOT IN ('information_schema',%s) ORDER BY n.nspname,c.relname
```

Query template at [source line 49](../src/chess_lakehouse/transfer/inventory.py#L49):

```sql
SELECT sha256(convert_to(row_to_json(t)::text,'UTF8')) FROM {} {} AS t
```

Query template at [source line 69](../src/chess_lakehouse/transfer/inventory.py#L69):

```sql
SELECT {} FROM ({} OFFSET 0) AS hashes(h)
```

Query template at [source line 80](../src/chess_lakehouse/transfer/inventory.py#L80):

```sql
SELECT n.nspname,c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='S'
        AND left(n.nspname,3)<>'pg_' AND n.nspname<>%s ORDER BY 1,2
```

Query template at [source line 83](../src/chess_lakehouse/transfer/inventory.py#L83):

```sql
SELECT last_value,is_called FROM {}
```

Query template at [source line 93](../src/chess_lakehouse/transfer/inventory.py#L93):

```sql
SELECT loid,pageno,data FROM pg_largeobject ORDER BY loid,pageno
```

Query template at [source line 102](../src/chess_lakehouse/transfer/inventory.py#L102):

```sql
SELECT oid FROM pg_largeobject_metadata m WHERE NOT EXISTS
        (SELECT 1 FROM pg_largeobject p WHERE p.loid=m.oid) ORDER BY oid
```

Query template at [source line 110](../src/chess_lakehouse/transfer/inventory.py#L110):

```sql
SELECT DISTINCT r.rolname FROM pg_roles r
        JOIN pg_shdepend d ON d.refobjid=r.oid AND d.refclassid='pg_authid'::regclass
        WHERE d.dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
        OR (d.dbid=0 AND d.classid='pg_database'::regclass AND
            d.objid=(SELECT oid FROM pg_database WHERE datname=current_database()))
```

Query template at [source line 116](../src/chess_lakehouse/transfer/inventory.py#L116):

```sql
SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,
                rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname
```

Query template at [source line 122](../src/chess_lakehouse/transfer/inventory.py#L122):

```sql
SELECT
        CASE WHEN setrole=0 THEN NULL ELSE pg_get_userbyid(setrole) END,setconfig
        FROM pg_db_role_setting WHERE setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database())
        ORDER BY setrole
```

Query template at [source line 126](../src/chess_lakehouse/transfer/inventory.py#L126):

```sql
SELECT
        CASE WHEN a.grantee=0 THEN NULL ELSE pg_get_userbyid(a.grantee) END,
        a.privilege_type,a.is_grantable FROM pg_database d,
        LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a
        WHERE d.datname=current_database() ORDER BY 1,2
```

Query template at [source line 148](../src/chess_lakehouse/transfer/inventory.py#L148):

```sql
SELECT EXISTS(
        SELECT 1 FROM pg_namespace WHERE left(nspname,3)<>'pg_'
          AND nspname NOT IN ('public','information_schema')
        UNION ALL SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
        UNION ALL SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
        UNION ALL SELECT 1 FROM pg_extension WHERE extname<>'plpgsql'
        UNION ALL SELECT 1 FROM pg_largeobject_metadata
        UNION ALL SELECT 1 FROM pg_event_trigger
        UNION ALL SELECT 1 FROM pg_publication
        UNION ALL SELECT 1 FROM pg_subscription
        UNION ALL SELECT 1 FROM pg_default_acl)
```

</details>

### `archive.py` — 2 templates

<details>
<summary>Expand SQL reference</summary>

Query template at [source line 72](../src/chess_lakehouse/transfer/archive.py#L72):

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY
```

Query template at [source line 73](../src/chess_lakehouse/transfer/archive.py#L73):

```sql
SELECT pg_export_snapshot()
```

</details>

### `upsert.py` — 29 templates

<details>
<summary>Expand SQL reference</summary>

Query template at [source line 21](../src/chess_lakehouse/transfer/upsert.py#L21):

```sql
WITH RECURSIVE parents(child,parent) AS (
        SELECT inhrelid,inhparent FROM pg_inherits UNION
        SELECT p.child,i.inhparent FROM parents p JOIN pg_inherits i ON i.inhrelid=p.parent)
        SELECT child::text,parent::text FROM parents
```

Query template at [source line 28](../src/chess_lakehouse/transfer/upsert.py#L28):

```sql
SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
                a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid),
                CASE WHEN a.attcollation=0 THEN NULL ELSE a.attcollation::regcollation::text END,
                ARRAY(SELECT enumlabel FROM pg_enum WHERE enumtypid=a.atttypid ORDER BY enumsortorder)
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
```

Query template at [source line 35](../src/chess_lakehouse/transfer/upsert.py#L35):

```sql
SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s
```

Query template at [source line 36](../src/chess_lakehouse/transfer/upsert.py#L36):

```sql
SELECT a.attname,i.indimmediate FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,position)
            JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
            WHERE i.indrelid=%s AND i.indisprimary AND k.position<=i.indnkeyatts ORDER BY k.position
```

Query template at [source line 42](../src/chess_lakehouse/transfer/upsert.py#L42):

```sql
SELECT pg_get_expr(relpartbound,oid) FROM pg_class WHERE oid=%s
```

Query template at [source line 43](../src/chess_lakehouse/transfer/upsert.py#L43):

```sql
SELECT format('%%I.%%I',n.nspname,c.relname)
            FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhparent JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE i.inhrelid=%s
```

Query template at [source line 46](../src/chess_lakehouse/transfer/upsert.py#L46):

```sql
SELECT n.nspname,p.proname,p.prosrc,t.tgtype::int,p.prosecdef,l.lanname
            FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_language l ON l.oid=p.prolang
            WHERE t.tgrelid=ANY(%s::oid[]) AND NOT t.tgisinternal AND t.tgenabled<>'D'
```

Query template at [source line 57](../src/chess_lakehouse/transfer/upsert.py#L57):

```sql
SELECT EXISTS(SELECT 1 FROM pg_rewrite WHERE ev_class=ANY(%s::oid[]))
```

Query template at [source line 60](../src/chess_lakehouse/transfer/upsert.py#L60):

```sql
SELECT EXISTS(SELECT 1 FROM pg_attribute a
            JOIN pg_type t ON t.oid=a.atttypid WHERE a.attrelid=%s AND a.attnum>0
            AND NOT a.attisdropped AND t.typtype NOT IN ('b','e'))
```

Query template at [source line 63](../src/chess_lakehouse/transfer/upsert.py#L63):

```sql
SELECT ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,position)
  JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
  WHERE k.position<=i.indnkeyatts ORDER BY k.position),
  coalesce((to_jsonb(i)->>'indnullsnotdistinct')::boolean,false)
FROM pg_index i WHERE i.indrelid=%s AND i.indisunique AND NOT i.indisprimary
  AND i.indexprs IS NULL AND i.indpred IS NULL
```

Query template at [source line 70](../src/chess_lakehouse/transfer/upsert.py#L70):

```sql
SELECT EXISTS(SELECT 1 FROM pg_index WHERE indrelid=%s
            AND indisunique AND (indexprs IS NOT NULL OR indpred IS NOT NULL))
```

Query template at [source line 72](../src/chess_lakehouse/transfer/upsert.py#L72):

```sql
SELECT
        c.conrelid::text,c.confrelid::text,
        ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(attnum,position)
              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.position),c.confmatchtype
        FROM pg_constraint c WHERE c.contype='f' ORDER BY c.oid
```

Query template at [source line 82](../src/chess_lakehouse/transfer/upsert.py#L82):

```sql
SELECT EXISTS(SELECT 1 FROM ONLY {} WHERE {})
```

Query template at [source line 139](../src/chess_lakehouse/transfer/upsert.py#L139):

```sql
SELECT sn.nspname,s.relname,tn.nspname,t.relname,a.attname,q.seqincrement,q.seqcycle
FROM pg_class s JOIN pg_sequence q ON q.seqrelid=s.oid
JOIN pg_namespace sn ON sn.oid=s.relnamespace JOIN pg_depend d ON d.objid=s.oid
  AND d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i')
JOIN pg_class t ON t.oid=d.refobjid JOIN pg_namespace tn ON tn.oid=t.relnamespace
JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid
WHERE d.refobjid=ANY(%s::oid[]) ORDER BY 1,2
```

Query template at [source line 167](../src/chess_lakehouse/transfer/upsert.py#L167):

```sql
SELECT sha256(convert_to(row_to_json(t)::text,'UTF8')) FROM {}
```

Query template at [source line 188](../src/chess_lakehouse/transfer/upsert.py#L188):

```sql
COPY {} ({}) FROM STDIN
```

Query template at [source line 197](../src/chess_lakehouse/transfer/upsert.py#L197):

```sql
CREATE TEMP TABLE upsert_chunk (LIKE {} INCLUDING GENERATED) ON COMMIT DROP
```

Query template at [source line 198](../src/chess_lakehouse/transfer/upsert.py#L198):

```sql
COPY upsert_chunk ({}) FROM STDIN
```

Query template at [source line 202](../src/chess_lakehouse/transfer/upsert.py#L202):

```sql
UPDATE {} AS t SET {} FROM upsert_chunk AS s WHERE {}
```

Query template at [source line 211](../src/chess_lakehouse/transfer/upsert.py#L211):

```sql
INSERT INTO {} AS t ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM upsert_chunk WHERE true ON CONFLICT ({}) {}
```

Query template at [source line 229](../src/chess_lakehouse/transfer/upsert.py#L229):

```sql
SELECT EXISTS(SELECT 1 FROM ONLY {} AS t JOIN {} AS s ON {} WHERE row_to_json(t)::text IS DISTINCT FROM row_to_json(s)::text)
```

Query template at [source line 236](../src/chess_lakehouse/transfer/upsert.py#L236):

```sql
SELECT EXISTS(SELECT 1 FROM ONLY {} AS t JOIN {} AS s ON {} WHERE NOT ({}))
```

Query template at [source line 263](../src/chess_lakehouse/transfer/upsert.py#L263):

```sql
SELECT last_value FROM {}
```

Query template at [source line 264](../src/chess_lakehouse/transfer/upsert.py#L264):

```sql
SELECT max({}) FROM {}
```

Query template at [source line 267](../src/chess_lakehouse/transfer/upsert.py#L267):

```sql
SELECT setval(%s::regclass,%s,true)
```

Query template at [source line 291](../src/chess_lakehouse/transfer/upsert.py#L291):

```sql
SELECT set_config('lock_timeout',%s,false)
```

Query template at [source line 297](../src/chess_lakehouse/transfer/upsert.py#L297):

```python
f'SELECT count(*) FROM {state.SCHEMA}.units WHERE job_id=%s'
```

Query template at [source line 309](../src/chess_lakehouse/transfer/upsert.py#L309):

```sql
CREATE TABLE {} (LIKE {} INCLUDING GENERATED)
```

Query template at [source line 310](../src/chess_lakehouse/transfer/upsert.py#L310):

```sql
ALTER TABLE {} ADD PRIMARY KEY ({})
```

</details>

### `state.py` — 13 templates

<details>
<summary>Expand SQL reference</summary>

Query template at [source line 61](../src/chess_lakehouse/transfer/state.py#L61):

```sql
SELECT pg_try_advisory_lock(hashtext('chess-database-transfer'))
```

Query template at [source line 63](../src/chess_lakehouse/transfer/state.py#L63):

```sql
SELECT to_regnamespace(%s)
```

Query template at [source line 69](../src/chess_lakehouse/transfer/state.py#L69):

```python
f"CREATE SCHEMA {SCHEMA}"
```

Query template at [source line 70](../src/chess_lakehouse/transfer/state.py#L70):

```python
f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC"
```

Query template at [source line 71](../src/chess_lakehouse/transfer/state.py#L71):

```python
f"""CREATE TABLE {SCHEMA}.jobs(id UUID PRIMARY KEY,config_hash TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,status TEXT NOT NULL,report JSONB NOT NULL DEFAULT '{{}}')"""
```

Query template at [source line 73](../src/chess_lakehouse/transfer/state.py#L73):

```python
f"""CREATE TABLE {SCHEMA}.units(job_id UUID NOT NULL REFERENCES {SCHEMA}.jobs,
                unit_id TEXT NOT NULL,checksum TEXT NOT NULL,rows BIGINT NOT NULL DEFAULT 0,
                committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(job_id,unit_id))"""
```

Query template at [source line 76](../src/chess_lakehouse/transfer/state.py#L76):

```python
f"INSERT INTO {SCHEMA}.jobs VALUES(%s,%s,%s,'running','{{}}')"
```

Query template at [source line 77](../src/chess_lakehouse/transfer/state.py#L77):

```python
f"SELECT config_hash,manifest_hash FROM {SCHEMA}.jobs WHERE id=%s"
```

Query template at [source line 79](../src/chess_lakehouse/transfer/state.py#L79):

```python
f"SELECT 1 FROM {SCHEMA}.jobs WHERE status<>'complete' LIMIT 1"
```

Query template at [source line 91](../src/chess_lakehouse/transfer/state.py#L91):

```python
f"SELECT unit_id,checksum,rows FROM {SCHEMA}.units WHERE job_id=%s"
```

Query template at [source line 96](../src/chess_lakehouse/transfer/state.py#L96):

```python
f"INSERT INTO {SCHEMA}.units(job_id,unit_id,checksum,rows) VALUES(%s,%s,%s,%s)"
```

Query template at [source line 102](../src/chess_lakehouse/transfer/state.py#L102):

```python
f"SELECT checksum,rows FROM {SCHEMA}.units WHERE job_id=%s AND unit_id=%s"
```

Query template at [source line 108](../src/chess_lakehouse/transfer/state.py#L108):

```python
f"UPDATE {SCHEMA}.jobs SET status='complete',report=%s WHERE id=%s"
```

</details>

### `service.py` — 7 templates

<details>
<summary>Expand SQL reference</summary>

Query template at [source line 29](../src/chess_lakehouse/transfer/service.py#L29):

```sql
SELECT rolname FROM pg_roles
```

Query template at [source line 37](../src/chess_lakehouse/transfer/service.py#L37):

```sql
SELECT EXISTS(SELECT 1 FROM pg_largeobject_metadata)
```

Query template at [source line 39](../src/chess_lakehouse/transfer/service.py#L39):

```sql
SELECT to_regnamespace(%s)
```

Query template at [source line 50](../src/chess_lakehouse/transfer/service.py#L50):

```sql
CREATE ROLE {} {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
```

Query template at [source line 59](../src/chess_lakehouse/transfer/service.py#L59):

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY
```

Query template at [source line 148](../src/chess_lakehouse/transfer/service.py#L148):

```sql
SELECT set_config('lock_timeout',%s,false)
```

Query template at [source line 151](../src/chess_lakehouse/transfer/service.py#L151):

```python
f"SELECT count(*) FROM {state.SCHEMA}.units WHERE job_id=%s"
```

</details>

### `verify-cloudsql-migration.py` — 6 templates

<details>
<summary>Expand SQL reference</summary>

Query template at [source line 42](../scripts/verify-cloudsql-migration.py#L42):

```sql
SET TRANSACTION READ ONLY
```

Query template at [source line 43](../scripts/verify-cloudsql-migration.py#L43):

```sql
SELECT status,report FROM {}.jobs WHERE id=%s
```

Query template at [source line 45](../scripts/verify-cloudsql-migration.py#L45):

```sql
SELECT rolsuper FROM pg_roles WHERE rolname=current_user
```

Query template at [source line 46](../scripts/verify-cloudsql-migration.py#L46):

```sql
SELECT current_user,current_database()
```

Query template at [source line 49](../scripts/verify-cloudsql-migration.py#L49):

```sql
SELECT count(*) FROM {}
```

Query template at [source line 52](../scripts/verify-cloudsql-migration.py#L52):

```sql
SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
            WHERE n.nspname=ANY(%s) AND NOT c.convalidated
```

</details>

<!-- SQL_REFERENCE_END -->
