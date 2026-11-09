# PostgreSQL objects and data transfer

Outline: prerequisites, plan/run, checkpoint recovery, verification, limits.

The Python entrypoint is `python -m chess_lakehouse.transfer`. It creates a native
directory-format `pg_dump`, restores schema objects through `pg_restore`, and
loads ordinary tables with bounded COPY chunks. Choose the mode explicitly:

| Mode | Existing tables/data | Result |
|---|---|---|
| `restore` (default) | Requires an empty destination | Restores native objects and data; refuses populated databases |
| `upsert` | Requires pre-provisioned compatible tables | Inserts missing primary keys, updates matching rows from the source, preserves destination-only rows |

**Both modes require a maintenance window with source and destination application
writers stopped.** Neither mode drops or truncates a populated table. This is a
database transfer, not ongoing replication or a whole-cluster clone.

## Prerequisites

Use source PostgreSQL 14–16 with the provided PostgreSQL 16 client image, and a
target at least as new as that client. Test matching server/client versions before
an upgrade; the local evidence uses PostgreSQL 16 on both sides. Create the target
database with source UTF8 encoding, collation and ctype. Required extensions and
their matching binaries must be available on the target. Source inventory reads
large objects, sequences, catalogs and all selected tables, including RLS-protected
data; this demo expects a trusted migration administrator with sufficient rights.
The destination must allow object/role creation under the selected policies.

Default owner and object ACL preservation requires existing owner/grantee roles.
`create_missing_roles=true` can create ordinary roles with their LOGIN/INHERIT
flags, **without passwords or privileged attributes**. Missing privileged roles
must be provisioned deliberately. Role memberships, cluster-wide role settings,
passwords, tablespace placement and replication setup are operator-managed.
Review existing destination role attributes and memberships separately; identical
object ACLs alone do not establish identical effective cluster privileges.
Tablespace placement is normalized to the target's default storage.

Reserve disk for the compressed archive, private SQL/logs and metadata, plus the
destination's tables, indexes, WAL and temporary index-building space. Source
database size is not a safe disk ceiling. Free-space checks enforce a reserve;
they cannot replace capacity planning. Disk checks during pg_dump sample once per
second. Exact content verification scans source and destination, and can dominate
the maintenance window on a multi-year dataset. No large-scale throughput estimate
is claimed from the small synthetic tests.

## Plan and run in Docker

```bash
cp .env.transfer.example .env.transfer
# Edit endpoints, TLS paths and credentials; never commit the real file.
# Create data/transfers writable by UID 10001 and mount your CA/secret files
# through a Compose override. Do not use host-only paths inside PGSSLROOTCERT.
docker compose -f compose.transfer.yaml build transfer
mkdir -p data/transfers
# One-time ownership setup for this new migration directory only:
docker run --rm --user 0:0 -v "$PWD/data/transfers:/migration" \
  --entrypoint sh chess-lakehouse-migrator:local -c 'chown 10001:10001 /migration'
docker compose -f compose.transfer.yaml run --rm transfer
```

The default command only checks the plan. It does not create roles or restore data.
Connection errors fail the command; they do not imply a successful plan. TLS is
`verify-full` in the example. For a local disposable PostgreSQL network only, set
both endpoint SSL modes to `disable`. Docker service names must resolve on a
network attached to this container. Do not publish a live database port simply to
make a test work. A network override can attach the transfer service to an existing
private Docker network.

After stopping writers, run with the **same work directory and options**:

```bash
docker compose -f compose.transfer.yaml run --rm transfer \
  --config /app/config/postgres-transfer.toml --workdir /migration/main \
  --run --maintenance-window
```

The explicit flag acknowledges both source and destination app writers are
quiesced. The tool checks the source again before completion; this detects changes
but does not stop other clients or keep them stopped after the command exits.
Keep the source offline until cutover is accepted. Standalone/embedded workers,
archive jobs, APIs, notebooks, schedules and external clients all count as writers.

Use `SOURCE_PGPASSWORD_FILE` / `TARGET_PGPASSWORD_FILE` for mounted credentials or
the corresponding plain password variables. Endpoints are isolated; unprefixed
PG connection variables are rejected rather than inherited. Explicit endpoint
fields or default libpq service files are supported; custom `PGSERVICEFILE` paths
are not supported for Python connections. Credentials are never command arguments
or tracker fields. Native archives, schema SQL and logs contain application data
and must be protected as sensitive backups.

## Resume and tracker

Repeat the same run command after an interruption. Keep the entire work directory:

| File/state | Purpose |
|---|---|
| `tracker.json` | Atomic, fsynced, mode-0600 progress summary and final report |
| `job.json` | Immutable job identifier and option binding for tracker recovery |
| `manifest.json`, `schema.sql`, `archive/` | Original snapshot, expected checksums, schema, row fingerprints and restore order |
| `dump.log`, `restore.log` | Private native-tool diagnostics |
| Destination `_chess_transfer.jobs`, `.units` | Authoritative committed-object/chunk ledger |

Each COPY chunk and its ledger marker commit in one transaction. Native object DDL
uses explicit BEGIN and sends COMMIT only after pg_restore succeeds. A crash after
the database commit but before the JSON update is reconciled from the destination
ledger; the chunk is not inserted twice. Checksums reject changed archive files or
chunk boundaries. Local file and destination advisory locks prevent duplicate
migration processes. The tracker is compact; per-unit lookups use the ledger's
primary key. An interrupted **dump restarts**; an interrupted **restore resumes**.
Compressed table files must be rescanned up to the resume point.

Upsert adds durable staging tables in `_chess_transfer` and a private
`upsert-baseline.json` file. Staging, merge and postponed foreign-key updates each
have their own transactional chunk markers. Keep the baseline and staging tables
until acceptance; they are needed to verify and resume the original merge.

If only the tracker is missing/corrupt, use the same command with
`--recover-tracker`. Recovery still requires `job.json`, the original intact
manifest/archive, the same semantic options, and the destination ledger. Do not
edit JSON or delete ledger rows to force a resume. Changing `chunk_rows`,
`chunk_bytes`, ownership, ACL or verification policy requires a new job and fresh
destination. Operational limits such as `max_row_bytes`, timeouts, disk reserve
and dump concurrency can be adjusted without changing chunk semantics.

## Completion checks and limits

A successful report requires exact row counts plus order-independent SHA256 row
fingerprints (duplicates retained), normalized native schema including selected
owners/ACLs, database comment/owner/settings/grants, sequence last-value/is-called,
and large-object page checksums. Physical partition/inheritance tables are counted
once. Generated columns are recomputed by PostgreSQL; identity values are copied.
Unlogged tables remain logged until completion to keep partial copies durable,
then regain source semantics. A completed unlogged table has PostgreSQL's normal
crash behavior; the transfer tool does not turn it into permanent durable storage.

Materialized views follow native pg_dump refresh semantics. If their restored
contents differ from the source (for example, a volatile query or stale source
view), verification fails. Resolve that case explicitly and start a fresh transfer;
do not bypass validation to cut over. Sequence SETVAL replay is idempotent but
sequence changes themselves are not transactional in PostgreSQL. There is no
logical-replication/zero-downtime promise, cluster-global clone, automatic role
password transfer, distributed restore, or automatic schema reconciliation.

## Upsert into an existing database

The agreed conflict policy is **source wins** on the complete primary key,
including composite keys. Source NULLs overwrite destination values too. Rows
whose keys occur only in the destination remain exactly as they were. Identical
rows avoid unnecessary updates. Tables, views, functions, permissions and database
settings stay in place; this mode merges table data rather than replaying DDL.

```bash
# Plan only; inspect table compatibility before stopping writers.
docker compose -f compose.transfer.yaml run --rm transfer \
  --config /app/config/postgres-upsert.toml --workdir /migration/upsert-001

# After pausing both source and destination writers:
docker compose -f compose.transfer.yaml run --rm transfer \
  --config /app/config/postgres-upsert.toml --workdir /migration/upsert-001 \
  --run --maintenance-window
```

You can alternatively pass `--mode upsert` with the existing transfer config.
Keep the **same mode, snapshot, workdir and chunk bounds** when resuming. A later
source snapshot needs a new workdir/job; completed ledgers are retained. Another
job cannot take over an unfinished migration on that destination.

Before merging, apply your versioned destination schema migrations and create
the required monthly partitions. Missing tables are reported; upsert does not
guess their definitions or ALTER incompatible tables. A non-deferrable primary
key and matching column types, generated expressions, keys, constraints and
partition placement are required for every source physical table. Keyless tables
are rejected rather than appended blindly. Destination-only tables are retained.

The tool first stages the complete source snapshot in logged tables, verifies it,
then records the expected union of source rows plus destination-only rows. It
merges chunks in foreign-key order. Nullable FK links, including the workspace's
active snapshot cycle, are filled after their referenced rows exist. Constraints
remain enabled. Final validation hashes the **whole resulting tables**, including
retained destination rows; whole-source/target equality would be incorrect here.
Additional capacity is needed for the full staged dataset, indexes, archive, WAL
and temporary chunks. Chunk commits are visible before overall completion: keep
the destination application offline until the final report succeeds.
Take a destination backup before the merge. The checksum baseline is not a backup
of overwritten values: reverting committed updates requires restoring that backup
or a separately reviewed reverse migration.

Owned ascending identity/serial sequences advance past retained destination IDs
and source sequence positions, never backwards. Standalone sequences and large
objects are not merged; a source containing large objects is rejected. Source
materialized views are not copied/refreshed in this mode; refresh destination
materialized views after accepting the merge if your application uses them.

Primary keys must identify the **same logical objects** in both databases. This
tool does not remap independently generated warehouse dimension IDs and their
foreign keys. A different primary key already holding the same alternate unique
identity is rejected before application rows are changed. Resolve that mapping
before merging independent lakehouses. Expression/partial unique indexes,
domain/composite column types, unlogged tables and non-nullable FK cycles need
an application-specific strategy and are rejected by this mode.

Custom write triggers/rules are rejected. The repository's exact published-history
guards are recognized and left enabled: new immutable records can be inserted,
identical records retained, but a source row that would update immutable history
is rejected before target-row changes. Immutable self-referencing history chains
must fit one configured bounded chunk so their references are satisfied in a
single INSERT. Larger histories are rejected before application-row writes.
Upsert does not override those existing data-model rules.
These checks complement normal PostgreSQL constraint enforcement; they do not
provide a general conflict-resolution engine for arbitrary schemas.

The insert/update behavior uses PostgreSQL's documented
[ON CONFLICT semantics](https://www.postgresql.org/docs/16/sql-insert.html).

After success, run application checks and volume verification while the source
remains offline. Retain the ledger and work directory for audit and rollback.
See [Kubernetes cutover](kubernetes.md) for ordering and rollback boundaries.

Native format and compatibility behavior follow the official
[PostgreSQL pg_dump documentation](https://www.postgresql.org/docs/16/app-pgdump.html)
and [pg_restore documentation](https://www.postgresql.org/docs/16/app-pgrestore.html).
