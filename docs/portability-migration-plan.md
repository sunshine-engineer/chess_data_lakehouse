# Organization, configuration, Kubernetes, and database transfer

1. Audit package boundaries, public docstrings, environment/configuration reads, hard-coded services/paths, worker lifecycle, and container settings. Record evidence and fix operationally significant issues without removing compatible legacy entrypoints.
2. Define documented configuration precedence and examples. Keep credentials in environment variables or mounted secret files, and provide a configuration check that never prints them.
3. Supply portable Kustomize manifests: application services, separate workers, one-off migration/training jobs, persistent storage, probes, resource limits, least-privilege security contexts, and optional development PostgreSQL. Document how to supply cluster-specific storage, images, networking and external PostgreSQL.
4. Build a containerized Python PostgreSQL transfer tool. Plan first; use an explicitly acknowledged maintenance window and an initially empty destination. Preserve native database objects with pg_dump/pg_restore; restore table data in bounded chunks with transactional destination markers and an atomic JSON tracker. Resume completed chunks and reconcile commits made before the tracker was updated. Never delete a populated destination automatically.
5. Verify schema/data/sequence state and archive integrity. Exercise failures before and after chunk commits, tracker recovery, ownership/ACL prerequisites, incompatible destinations, and corrupted artifacts against disposable Docker databases.
6. Render and validate Kubernetes manifests in a container, run relevant application regressions, document the cutover/rollback workflow and storage transfer, and leave the existing live repertoire build intact. A real cluster deployment and real destination migration require the user's chosen endpoints and storage class; local validation will be labelled accordingly.

The user selected a portable setup (cluster not chosen) and accepted a maintenance window with source writes stopped. No cloud resources or real destination database will be changed as part of testing.

All six implementation/validation milestones are complete locally. See
[validation evidence](portability-validation.md) and the
[cutover runbook](kubernetes.md) for the remaining target-specific acceptance work.
