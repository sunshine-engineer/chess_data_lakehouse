#!/usr/bin/env bash
# Runs only in the PostgreSQL tools container. Published state plus Parquet artifacts.
set -euo pipefail
umask 077
bundle="/backups/$(date -u +%Y%m%dT%H%M%S)-${RANDOM}"
mkdir -p "$bundle" /reports
holder_out=$(mktemp)
holder_err=$(mktemp)
restore_dir=$(mktemp -d)
restore_db="chess_restore_${RANDOM}_${RANDOM}"
holder_pid=''
backend_pid=''
created=false
cleanup() {
  if [[ "$created" == true ]]; then dropdb --if-exists --force "$restore_db" >/dev/null 2>&1 || true; fi
  if [[ "$backend_pid" =~ ^[0-9]+$ ]]; then psql -XqAt -c "SELECT pg_terminate_backend($backend_pid)" >/dev/null 2>&1 || true; fi
  if [[ -n "$holder_pid" ]]; then kill "$holder_pid" >/dev/null 2>&1 || true; wait "$holder_pid" >/dev/null 2>&1 || true; fi
  rm -rf "$restore_dir"
  rm -f "$holder_out" "$holder_err"
}
trap cleanup EXIT INT TERM
# Hold all publication locks and export one consistent source snapshot.
stdbuf -oL psql -XqAt -v ON_ERROR_STOP=1 >"$holder_out" 2>"$holder_err" <<'SQL' &
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL lock_timeout='30s';
SELECT pg_backend_pid();
SELECT pg_advisory_xact_lock(hashtext('chess-v2-python'));
SELECT pg_advisory_xact_lock(hashtext('chess-v2-python_mp'));
SELECT pg_advisory_xact_lock(hashtext('chess-v2-spark'));
SELECT pg_advisory_xact_lock(hashtext('personal-study-publication'));
SELECT pg_advisory_xact_lock(hashtext('academy-publication'));
SELECT pg_export_snapshot();
SELECT pg_sleep(3600);
COMMIT;
SQL
holder_pid=$!
snapshot=''
for ((attempt=0; attempt<400; attempt++)); do
  backend_pid=$(awk 'NF {print;exit}' "$holder_out")
  snapshot=$(awk '/^[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+$/ {print;exit}' "$holder_out")
  if [[ -n "$snapshot" ]]; then break; fi
  if ! kill -0 "$holder_pid" 2>/dev/null; then cat "$holder_err" >&2; exit 1; fi
  sleep 0.1
done
if [[ -z "$snapshot" ]]; then echo 'Could not acquire a consistent backup snapshot' >&2; exit 1; fi
academy_schema=()
if [[ $(psql -XqAt -c "SELECT to_regnamespace('academy') IS NOT NULL") == t ]]; then academy_schema=(--schema=academy); fi
pg_dump --format=custom --no-owner --snapshot="$snapshot" "${academy_schema[@]}" \
  --schema=control --schema=staging --schema=warehouse_python \
  --schema=warehouse_python_mp --schema=warehouse_spark --schema=study -f "$bundle/warehouse.dump"
# The source volume is mounted read-only; immutable shard checksums are preserved.
tar --exclude='*.tmp' --exclude='*.part' -cf "$bundle/pipeline.tar" -C /data .
# The personal publication lock blocks new immutable files until the snapshot and
# artifact bundle have been captured. Hidden temporary directories are not published.
tar --exclude='./.*' --exclude='./workspace/.*' --exclude='./workspace/upload-*' \
  --exclude='./workspace/tmp' --exclude='./workspace/exports' \
  -cf "$bundle/study.tar" -C /study .
(
  cd "$bundle"
  sha256sum warehouse.dump pipeline.tar study.tar > SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)
fingerprint() {
  psql --dbname="$1" -XqAt -v ON_ERROR_STOP=1 -v use_snapshot="$2" -v snapshot="$snapshot" <<'SQL'
BEGIN ISOLATION LEVEL REPEATABLE READ;
\if :use_snapshot
SET TRANSACTION SNAPSHOT :'snapshot';
\endif
SET LOCAL TIME ZONE 'UTC';
SELECT format('SELECT %L || ''|'' || count(*) || ''|'' || coalesce(sum(hashtextextended(row_to_json(t)::text,0)::numeric),0) FROM %I.%I t',
              n.nspname||'.'||c.relname,n.nspname,c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname IN ('control','staging','warehouse_python','warehouse_python_mp','warehouse_spark','study','academy')
AND c.relkind='r' ORDER BY n.nspname,c.relname
\gexec
COMMIT;
SQL
}
fingerprint "$PGDATABASE" true > "$bundle/source-fingerprints.txt"
createdb "$restore_db"
created=true
pg_restore --exit-on-error --no-owner --dbname="$restore_db" "$bundle/warehouse.dump"
fingerprint "$restore_db" false > "$bundle/restored-fingerprints.txt"
cmp "$bundle/source-fingerprints.txt" "$bundle/restored-fingerprints.txt"
invalid=$(psql --dbname="$restore_db" -XqAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE (n.nspname LIKE 'warehouse_%' OR n.nspname IN ('study','academy')) AND NOT c.convalidated")
[[ "$invalid" == 0 ]]
# ACLs are part of the backup: reader access survives and writes remain forbidden.
PGUSER=chess_reader PGPASSWORD="$READER_PASSWORD" psql --dbname="$restore_db" -XqAt -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM warehouse_python.fact_game' >/dev/null
if PGUSER=chess_reader PGPASSWORD="$READER_PASSWORD" psql --dbname="$restore_db" -XqAt -v ON_ERROR_STOP=1 -c 'DELETE FROM warehouse_python.fact_game' >/dev/null 2>&1; then
  echo 'Restored reader unexpectedly has write permission' >&2
  exit 1
fi
tar -xf "$bundle/pipeline.tar" -C "$restore_dir"
tar -df "$bundle/pipeline.tar" -C "$restore_dir" >/dev/null
mkdir -p "$restore_dir/study"
tar -xf "$bundle/study.tar" -C "$restore_dir/study"
tar -df "$bundle/study.tar" -C "$restore_dir/study" >/dev/null
study_exists=$(psql --dbname="$restore_db" -XqAt -c "SELECT to_regclass('study.games') IS NOT NULL")
if [[ "$study_exists" == t ]]; then
  if PGUSER=chess_reader PGPASSWORD="$READER_PASSWORD" psql --dbname="$restore_db" -XqAt -v ON_ERROR_STOP=1 -c 'SELECT * FROM study.games LIMIT 1' >/dev/null 2>&1; then
    echo 'Archive reader unexpectedly has private-study access after restore' >&2
    exit 1
  fi
fi
# Ensure the restore tool rejects a deliberately truncated backup.
head -c 128 "$bundle/warehouse.dump" > "$bundle/corrupt.dump"
if pg_restore --list "$bundle/corrupt.dump" >/dev/null 2>&1; then
  echo 'Corrupt backup unexpectedly passed validation' >&2
  exit 1
fi
rm "$bundle/corrupt.dump"
tables=$(wc -l < "$bundle/source-fingerprints.txt")
printf '{"status":"passed","bundle":"%s","tables_verified":%s,"sql_row_counts_and_fingerprints_match":true,"parquet_bundle_restored":true,"private_study_bundle_restored":true,"constraints_validated":true,"reader_permissions_validated":true,"corrupt_backup_rejected":true}\n' "$bundle" "$tables" > /reports/backup-restore.json
cat /reports/backup-restore.json
