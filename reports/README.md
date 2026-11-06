# Generated benchmark reports

`ml-evaluation.json` contains actual January model measurements and provenance for
the three ML case studies. `browser/acceptance.json` and screenshots record executed
web interactions. These are separate from the historical ingestion benchmarks.

The current historical pipelines are measured in [comparison-v2.md](comparison-v2.md),
with all nine samples, semantic equality checks and query plans in
[comparison-v2.json](comparison-v2.json). Operational reports cover
[database crash recovery](database-crash-recovery.json),
[two-worker synthetic Spark execution](cluster-smoke.json), and
[populated warehouse backup/restore](backup-restore.json).
See [optimized validation](../docs/optimized-validation.md) for scope and provenance.

The earlier `comparison.md` is the baseline measured comparison; `comparison.json`
contains raw per-run statistics and equality checks. Engine JSON files preserve
individual sample timings. Generate them with the commands in `docs/docker.md`.

These are local measurements, not performance promises. Reports contain no
passwords or player usernames. Bulk data and PostgreSQL files live in named Docker
volumes and are not included in this directory.

See [Docker execution evidence](../docs/docker-validation.md) for tested recovery
behavior, the persistence readback, image provenance, and benchmark limitations.
