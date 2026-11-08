# Code organization review

Outline: responsibilities, concrete changes, and remaining boundaries.

The package already separates ingestion, analytics/ML and personal study features.
Preserving those working interfaces is more useful than moving every file during
deployment work. Current responsibilities:

| Area | Responsibility |
|---|---|
| Top-level pipeline modules | Compatible first-version Python/Spark entrypoints |
| `optimized/` | Historical archive preparation, partitioned warehouse, resumable processing, archive supervisor |
| `personal/` | PGN parsing, bounded engine evidence, legacy personal-service support |
| `personal/workspace/` | Accounts, versioned libraries, imports, saved studies, boards, learning and repertoire queues |
| `ml/`, `web/` | Model artifacts and browser/API presentation |
| `runtime.py`, `serve.py`, `health.py` | Deployment configuration, WSGI launch and probes |
| `transfer/` | Independent offline PostgreSQL objects/data migration |
| `volumes.py` | Read-only file-volume integrity checks |
| `config/`, `deploy/`, `docker/` | Domain/deployment settings, Kubernetes resources and container builds |

Changes made for portability:

- Central deployment settings with strict validation, explicit precedence and mounted
  secret-file support. Database adapters consistently read mounted passwords.
- Configurable WSGI launch; foreground study-worker supervision, readiness/liveness,
  SIGTERM draining, and separate API/worker deployment. Existing embedded Compose
  mode remains supported through its explicit setting.
- Public top-level functions have task-specific docstrings; a generated
  [API index](python-api.md) and Docker checker keep the inventory current.
  Dataclass fields, immutable SQL migrations and existing domain configuration
  remain close to their implementation. The checker covers top-level APIs rather
  than claiming every nested callback has a separate documentation contract.
- Transfer concerns are split into typed options/endpoints, durable state,
  inventory/fingerprints, archive handling, transactional restore, and orchestration.
  The CLI plans by default and has no automatic destructive destination cleanup.
  `transfer/upsert.py` adds a separate source-wins data merge path with logged
  staging, FK ordering, preservation baselines and mode-specific verification.
- Kubernetes and disposable QA configurations are separate from live Compose
  volumes and image tags. Separate jobs make maintenance ordering reviewable.

Boundaries worth keeping visible:

- Existing workspace modules contain compact one-line statements and large request
  handlers. They remain maintainable for this demo but should be split by feature
  when a behavior change needs it. A wholesale formatting/refactoring change would
  obscure operational review and carries no proven product benefit here.
- Compatible legacy entrypoints duplicate parts of optimized ingestion and the
  private study API. Documented compatibility is retained; deprecation needs a
  user-facing release/migration plan before deletion.
- SQL/schema role names and absolute stored artifact paths are explicit contracts,
  not freely interchangeable environment values. Portable deployment preserves
  those contracts and parameterizes infrastructure around them.
- One foreground worker per shared root is the tested design. More replicas,
  zero-downtime replication, automatic cloud provisioning, provider authentication,
  and multi-terabyte capacity guarantees are outside this change.
- Engine/review cache keys include implementation-file hashes. Documentation
  changes in those files therefore create new cache keys when the new image is
  deployed. Stored completed evidence remains available; queued reviews with old
  settings may need a new request. Existing live workers retain their current
  image until an explicit deployment/cutover.

Use [configuration](configuration.md), [Kubernetes cutover](kubernetes.md), and
[PostgreSQL transfer](postgres-transfer.md) together. Validation evidence belongs
in [portability validation](portability-validation.md); a manifest schema pass
alone is not evidence of deployment or recoverability on a real cluster.
