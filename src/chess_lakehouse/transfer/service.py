"""Plan fresh restores or source-wins upserts without dropping destination tables."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from psycopg import sql

from . import archive, inventory, restore, state


def plan(source, target, options):
    """Check server compatibility, destination state and required owner/ACL roles."""
    with source.connect() as src, target.connect() as dst:
        a, b = inventory.identity(src), inventory.identity(dst)
        major = archive.client_major()
        if a["server_version"] < 140000:
            raise ValueError("Source PostgreSQL 14 or newer is required for server-side SHA256 verification")
        if a["server_version"]//10000 > major or b["server_version"]//10000 < major:
            raise ValueError("Use a pg_dump client at least as new as the source and no newer than the target")
        if a["database"] == b["database"] and a["oid"] == b["oid"] and a.get("system_identifier") and a["system_identifier"] == b.get("system_identifier"):
            raise ValueError("Source and destination are the same database")
        if any(a[k] != b[k] for k in ("encoding", "collation", "ctype")) or a["encoding"] != "UTF8":
            raise ValueError("Create the destination with the source's UTF8 encoding, collation and ctype")
        required = inventory.roles(src) if options.mode=='restore' and (options.ownership == "preserve" or options.privileges == "preserve") else []
        existing = {row[0] for row in dst.execute("SELECT rolname FROM pg_roles")}
        missing = [r for r in required if r["name"] not in existing]
        if missing and (not options.create_missing_roles or any(r["privileged"] for r in missing)):
            raise ValueError("Pre-create required destination roles: " + ", ".join(r["name"] for r in missing) + ". Privileged attributes/passwords are never copied automatically.")
        merge = None
        if options.mode == 'upsert':
            from . import upsert
            merge = upsert.preflight(upsert.catalog(src), dst)
            if src.execute('SELECT EXISTS(SELECT 1 FROM pg_largeobject_metadata)').fetchone()[0]:
                raise ValueError('Upsert does not merge large-object OIDs; use a fresh-database restore')
        elif not dst.execute("SELECT to_regnamespace(%s)", (state.SCHEMA,)).fetchone()[0]:
            if inventory.destination_has_objects(dst): raise ValueError("Destination is not empty. No objects were deleted.")
    return dict(source=source.public(), target=target.public(), source_identity=a, target_identity=b,
                roles_to_create=missing, maintenance_window_required=True, options_hash=options.fingerprint(),
                mode=options.mode, upsert=merge)


def prepare_roles(conn, roles):
    """Create explicitly allowed missing ordinary roles without passwords or elevation."""
    with conn.transaction():
        for role in roles:
            conn.execute(sql.SQL("CREATE ROLE {} {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(
                sql.Identifier(role["name"]), sql.SQL("LOGIN" if role["login"] else "NOLOGIN"),
                sql.SQL("INHERIT" if role["inherit"] else "NOINHERIT")))


def validate(root, target, metadata, options):
    """Compare all selected table contents, native schema, sequences and large objects."""
    failures = []
    with target.connect() as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        actual = inventory.relations(conn)
        by_name = {(r["schema"], r["name"]): r for r in actual.values()}
        for oid, expected in metadata["fingerprints"].items():
            relation = metadata["relations"][oid]
            selected = by_name.get((relation["schema"], relation["name"]))
            if not selected or inventory.rows_digest(conn, selected) != expected:
                failures.append(relation["schema"]+"."+relation["name"])
            if selected and selected['persistence']!=relation['persistence'] and not (relation['persistence']=='u' and selected['persistence']=='p'):
                failures.append("table persistence")
        if inventory.sequences(conn) != metadata["sequences"]: failures.append("sequence state")
        if inventory.large_objects(conn) != metadata["large_objects"]: failures.append("large objects")
        actual_identity = inventory.identity(conn)
        if actual_identity['comment'] != metadata['identity']['comment']:
            failures.append('database comment')
        if options.ownership=='preserve' and actual_identity['owner']!=metadata['identity']['owner']:
            failures.append('database owner')
        actual_options=inventory.database_options(conn)
        for key in ('settings','grants') if options.privileges=='preserve' else ('settings',):
            if sorted(map(inventory.stable_json,actual_options[key]))!=sorted(map(inventory.stable_json,metadata['database_options'][key])):
                failures.append('database '+key)
    # The ledger schema is excluded from both sides' native schema dumps.
    if archive.schema_digest(archive.schema_sql(target, options)) != metadata["schema_sha256"]:
        failures.append("schema/owners/ACLs")
    if failures:
        raise ValueError("Migration validation failed for: " + ", ".join(failures[:20]))
    return dict(tables_verified=len(metadata["fingerprints"]), schema_verified=True,
                sequences_verified=True, large_objects_verified=True,
                rows_verified=sum(item["rows"] for item in metadata["fingerprints"].values()))


def source_unchanged(source, metadata, options):
    """Refuse cutover if writes changed the source after the archived snapshot."""
    if not options.verify_source_unchanged: return
    with source.connect() as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        actual = inventory.relations(conn)
        for oid, expected in metadata["fingerprints"].items():
            if oid not in actual or actual[oid]['persistence']!=metadata['relations'][oid]['persistence'] or inventory.rows_digest(conn, actual[oid]) != expected:
                raise ValueError("Source changed after the snapshot; keep the destination offline and plan a fresh cutover")
        if inventory.sequences(conn) != metadata["sequences"] or inventory.large_objects(conn) != metadata["large_objects"]:
            raise ValueError("Source sequence/large-object state changed after the snapshot")
        identity=inventory.identity(conn)
        if identity!=metadata['identity'] or inventory.database_options(conn)!=metadata['database_options']:
            raise ValueError('Source database identity/options changed after the snapshot')
    if archive.schema_digest(archive.schema_sql(source, options)) != metadata["schema_sha256"]:
        raise ValueError("Source schema changed after the snapshot")


def run(source, target, options, workdir, maintenance_window=False, recover_tracker=False, inject=None):
    """Transfer one database; safely reconcile a tracker that lags committed chunks.

    `inject` is a test callback for crash windows; it is never populated from CLI
    input. Source/target writers must be quiesced by the operator. This function
    never drops or truncates an existing destination.
    """
    if not maintenance_window: raise ValueError("Acknowledge a maintenance window with writers stopped")
    os.umask(0o077)
    root = Path(workdir)
    with state.lock(root):
        checked = plan(source, target, options)
        tracker_path = root/"tracker.json"
        if tracker_path.exists():
            try: tracker = json.loads(tracker_path.read_text())
            except ValueError:
                if not recover_tracker: raise ValueError("Tracker is corrupt; use --recover-tracker with the original work directory") from None
                tracker = None
        else: tracker = None
        if tracker is None:
            identity_path = root/"job.json"
            if identity_path.exists():
                if not recover_tracker: raise ValueError("Tracker is missing; use --recover-tracker")
                tracker = json.loads(identity_path.read_text())
            else:
                tracker = dict(version=1, job_id=str(uuid4()), options_hash=options.fingerprint(), phase="planned", completed={})
                state.atomic_json(identity_path, tracker)
        if tracker["options_hash"] != options.fingerprint(): raise ValueError("Migration options changed; use the original options to resume")
        try:
            if not (root/"manifest.json").exists():
                if (root/"archive").exists(): shutil.rmtree(root/"archive")
                tracker["phase"] = "dumping"; state.atomic_json(tracker_path, tracker)
                archive.build(root, source, options)
            metadata = archive.verify(root)
            manifest_hash = state.digest(root/"manifest.json")
            if options.mode == 'upsert':
                from . import upsert
                return upsert.run(source, target, options, root, metadata, manifest_hash, tracker, inject)
            with target.connect(autocommit=True) as conn:
                state.bootstrap(conn, tracker["job_id"], options.fingerprint(), manifest_hash)
                conn.execute("SELECT set_config('lock_timeout',%s,false)", (str(options.lock_timeout_seconds*1000),))
                prepare_roles(conn, checked["roles_to_create"])
                tracker.pop("completed", None)
                tracker["units_committed"] = conn.execute(f"SELECT count(*) FROM {state.SCHEMA}.units WHERE job_id=%s", (tracker["job_id"],)).fetchone()[0]
                def checkpoint(changed):
                    if changed:
                        tracker["units_committed"] += 1
                        state.atomic_json(tracker_path, tracker)
                tracker["phase"] = "restoring"; state.atomic_json(tracker_path, tracker)
                for entry in metadata["pre_order"]:
                    checkpoint(restore.native(root, target, conn, tracker["job_id"], entry, options, inject))
                restore.protect_unlogged(conn, tracker['job_id'], metadata['relations'])
                for entry in metadata["data_order"]:
                    if entry["table_data"]:
                        relation = metadata["relations"][entry["oid"]]
                        for number, data, rows in archive.chunks(root, entry, options):
                            if shutil.disk_usage(root).free < options.min_free_bytes: raise OSError("Migration disk reserve reached")
                            tracker["last_table"] = [relation["schema"], relation["name"]]
                            checkpoint(restore.copy_chunk(conn, tracker["job_id"], entry, relation, number, data, rows, inject))
                    else:
                        checkpoint(restore.native(root, target, conn, tracker["job_id"], entry, options, inject))
                for entry in metadata["post_order"]:
                    checkpoint(restore.native(root, target, conn, tracker["job_id"], entry, options, inject))
                restore.database_metadata(conn, tracker["job_id"], metadata, options)
                tracker["phase"] = "validating"; state.atomic_json(tracker_path, tracker)
                report = validate(root, target, metadata, options)
                source_unchanged(source, metadata, options)
                report.update(status="complete", finished_at=datetime.now(timezone.utc).isoformat(),
                              units_committed=conn.execute(f"SELECT count(*) FROM {state.SCHEMA}.units WHERE job_id=%s", (tracker["job_id"],)).fetchone()[0])
                with conn.transaction():
                    restore.restore_persistence(conn, metadata['relations'])
                    state.finish(conn, tracker["job_id"], report)
                tracker.update(phase="complete", report=report, units_committed=report["units_committed"])
                tracker.pop("error_type",None)
                state.atomic_json(tracker_path, tracker)
                return report
        except BaseException as exc:
            tracker.update(phase="interrupted" if not isinstance(exc, Exception) else "failed", error_type=type(exc).__name__)
            state.atomic_json(tracker_path, tracker)
            raise
