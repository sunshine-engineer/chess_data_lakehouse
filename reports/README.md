# Generated benchmark reports

`comparison.md` is the human-readable measured comparison; `comparison.json`
contains raw per-run statistics and equality checks. Engine JSON files preserve
individual sample timings. Generate them with the commands in `docs/docker.md`.

These are local measurements, not performance promises. Reports contain no
passwords or player usernames. Bulk data and PostgreSQL files live in named Docker
volumes and are not included in this directory.

See [Docker execution evidence](../docs/docker-validation.md) for tested recovery
behavior, the persistence readback, image provenance, and benchmark limitations.
