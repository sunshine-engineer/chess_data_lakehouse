# Device transfer and restore — 11 November 2026

Outline: recover code branches, download public assets, verify bytes, restore files,
configure fresh secrets, and identify private state that still needs a separate backup.

## What this backup covers

The selected repository, `sunshine-engineer/chess_data_lakehouse`, is **public**.
The owner explicitly requested publication to the same repository, replacement of
sensitive values with dummy values, and exclusion of sensitive backup files.
The backup is therefore a sanitized recovery copy, not a full private-device backup.

Dated branches use `backup/<original-branch>-YYYYMMDD-HHMM` (IST). Local branches
and distinct remote-tracking tips are preserved without moving the existing
branches. A working-copy branch includes the previously uncommitted migration
documentation, project notes, notebook and this runbook. See
`reports/device-backup/branches.json` for the source-ref to backup-ref mapping.
No force push or original branch deletion is part of the procedure.

The associated GitHub release holds ZIP archives, per-file manifests and SHA256SUMS.
It includes eligible workspace files (including ignored virtual-environment files),
public archive pipeline data, trained public-data models, reviewed January-2013 SQLite warehouses and downloaded LLM model
files. Empty directories are represented where applicable. `.env` and password
files are represented with dummy values. Notebook execution outputs are cleared.
Git's object database/configuration/reflogs are not placed in the ZIP; published
branch ancestry preserves reachable commit history after a credential scan.

## Deliberate exclusions and deletion gate

These items are **not** published to this public repository:

- Personal PGN exports, private account/owner information and row-level diagnostics.
- Migration workdirs, native SQL database dumps, unreviewed database files and their
  private metadata/checkpoints. The earlier Cloud SQL copy is an older snapshot;
  it is not evidence that all later local study writes are recoverable.
- PostgreSQL Docker volume contents, study files/volumes and backup volumes.
- Unreviewed ignored screenshots/diagnostic reports.
- Original passwords, tokens, device SSH keys, gcloud credentials and other
  operator identities. Dummy secret files cannot authenticate to existing services.

A local, ignored `data/device-backup/<inventory-id>/excluded-files.private.json`
records exact file exclusions. Published coverage contains counts and categories,
not private row data. The Docker inventory records other project volumes whose
contents were intentionally excluded.

**Whole-project/device deletion is NOT approved by this backup.** Private database
and study state remains a device dependency unless it is backed up separately to
an owner-approved private destination and successfully restored. Nothing in this
procedure deletes the project or Docker volumes. This run does not claim a complete
PostgreSQL/application restore. A verified ZIP roundtrip establishes file recovery
for the included files only.

## Recover on the new device

Install Git, GitHub CLI and Docker Desktop/Engine. Authenticate GitHub CLI as the
repository owner; do not copy the old device's credential cache. Clone the repository:

```powershell
git clone https://github.com/sunshine-engineer/chess_data_lakehouse.git
Set-Location chess_data_lakehouse
git fetch origin '+refs/heads/backup/*:refs/remotes/origin/backup/*'
gh release list --repo sunshine-engineer/chess_data_lakehouse
```

Use the release/tag and working-copy branch named in
`reports/device-backup/publication.json`, then check out that branch. A Git clone
**does not download release assets**. Download them into an external recovery
folder, keeping archive names unchanged:

```powershell
# Replace these two values with the published receipt's branch/tag.
git switch --track origin/backup/working-copy-YYYYMMDD-HHMM
$releaseTag = 'device-backup-YYYYMMDD-HHMM'
New-Item -ItemType Directory -Force -Path ../chess-recovery-assets | Out-Null
gh release download $releaseTag --repo sunshine-engineer/chess_data_lakehouse `
  --dir ../chess-recovery-assets

docker build -f docker/ops.Dockerfile -t chess-lakehouse-ops:restore .
$assets = (Resolve-Path ../chess-recovery-assets).Path
docker run --rm --network none --mount "type=bind,source=$assets,target=/assets,readonly" `
  --mount "type=bind,source=$PWD,target=/workspace,readonly" `
  --entrypoint python chess-lakehouse-ops:restore /workspace/scripts/device_backup.py `
  verify --directory /assets
```

Restore the workspace ZIP into a **new empty folder**, not over a clone with files:

```powershell
New-Item -ItemType Directory -Force -Path ../chess-restored-workspace | Out-Null
$target = (Resolve-Path ../chess-restored-workspace).Path
docker run --rm --network none --user 0:0 `
  --mount "type=bind,source=$assets,target=/assets,readonly" `
  --mount "type=bind,source=$PWD,target=/workspace,readonly" `
  --mount "type=bind,source=$target,target=/restored" `
  --entrypoint python chess-lakehouse-ops:restore /workspace/scripts/device_backup.py `
  restore --directory /assets --archive workspace --target /restored
```

The helper checks asset hashes and every file hash, rejects unsafe paths and
symlinks, and refuses a nonempty destination. The restored workspace has no `.git`.
Keep the clone separately for branch/history recovery, or initialize a new Git
checkout and connect it to the dated working-copy branch after preserving the
restored files. Recreate the virtual environment from lock files on the new
platform; retained old `.venv` files are archival, not a portable runtime.

## Restore public Docker files

The non-workspace ZIP archive names match Docker volume names. Read `PUBLIC_BACKUP_COVERAGE.json`
for the actual archives. Create a fresh volume and use the helper to populate it;
repeat for each included non-database volume:

```powershell
# Example: archive chess-lakehouse_pipeline-data.zip
docker volume create chess-recovered_pipeline-data
docker run --rm --network none --user 0:0 `
  --mount "type=bind,source=$assets,target=/assets,readonly" `
  --mount "type=bind,source=$PWD,target=/workspace,readonly" `
  --mount type=volume,source=chess-recovered_pipeline-data,target=/restored `
  --entrypoint python chess-lakehouse-ops:restore /workspace/scripts/device_backup.py `
  restore --directory /assets --archive chess-lakehouse_pipeline-data --target /restored
```

Use volumes whose names match your new Compose project, or a reviewed volume-name
override. Do not copy these public archives into a PostgreSQL data directory.
The old Ollama device key is omitted; Ollama generates a new identity on startup.
The optional QA PostgreSQL volumes are not required to recreate disposable tests.

## Configure a fresh installation or restore private state

1. Replace every `DUMMY_REPLACE_ON_NEW_DEVICE` value in recovered env/secret files
   with newly generated values. Configure Cloud SQL identifiers and authenticate
   gcloud again on the new device if using that service. Dummy values do not rotate
   the old server's passwords: revoke/rotate old credentials separately as needed.
2. For an empty demo, build/start the existing Compose services and run the normal
   migrations described in README.md. Reimport public months if database state is
   not being restored. Public Parquet/PGN recovery does not populate PostgreSQL by itself.
3. For exact personal-library recovery, obtain a separately secured database/study
   backup, restore it in an isolated environment and validate row counts, study
   artifacts and application behavior before switching devices. See
   `docs/postgres-transfer.md` and `docs/cloudsql-migration-handover.md` for database
   migration semantics. Their older ignored native artifacts are excluded here.
4. Compare branch commit IDs, downloaded hashes and restored member hashes against
   the published receipts. Then test login, historical queries, personal study
   navigation, model loading and required background jobs. Do not infer these
   checks passed from file checksums alone.

## Commands used to prepare and verify

All Python backup code executes in `chess-lakehouse-ops:local` in Docker.
The original workspace/configuration is mounted read-only when creating archives;
only the ignored output directory is writable. Secrets are read only for redaction
and checks, never printed or uploaded. Original running configurations are unchanged.

- `git for-each-ref` and `git rev-list --objects --all` inventory every branch/tip
  and reachable historical object. Known live credentials/private-key patterns are
  checked in uncompressed Git objects before original ancestry is published.
- `scripts/device_backup.py scan` records the historical credential scan.
- `scripts/device_backup.py build` classifies files, redacts eligible copies,
  writes ZIPs, reads each member back, and records manifests/exclusions/checksums.
- Native Git creates dated backup refs, with a sanitized working-copy commit.
- `gh release create` / `gh release upload` publish the eligible archives.
- `git ls-remote` verifies published branch tips. `gh release download` downloads
  the published bytes to a separate directory; the helper then verifies all assets
  and restored member hashes. File restore is rehearsed using those downloaded assets.

Exact branch and release receipts accompany this document after publication.
If any publish/download/verification step is blocked or fails, its status is
reported explicitly; local archives alone are not a completed GitHub backup.

### Publication command record for this run

```powershell
# Historical refs: the JSON receipt maps every original tip to its dated name.
git branch --no-track backup/CXOED/cloudsql-managed-migration-20261111-0405 8c72117acd0e7ff03cbe5ab69b5da32ae438c94d
# All 16 original-history backup refs were pushed together with git push --atomic.
# The sanitized working-copy commit used a separate GIT_INDEX_FILE and work tree.
git push origin refs/heads/backup/working-copy-20261111-0405:refs/heads/backup/working-copy-20261111-0405

# ZIPs, manifests, coverage and checksums were passed as explicit asset paths.
gh release create device-backup-20261111-0405 `
  --repo sunshine-engineer/chess_data_lakehouse `
  --target backup/working-copy-20261111-0405 `
  --title 'Sanitized device backup device-backup-20261111-0405' `
  --notes-file <local-release-notes.md> <asset-paths>
gh release download device-backup-20261111-0405 `
  --repo sunshine-engineer/chess_data_lakehouse --dir <empty-download-directory>

# Independent Git recovery check used a fresh clone, not the original object store.
git clone --mirror https://github.com/sunshine-engineer/chess_data_lakehouse.git <new-history-directory>
git --git-dir=<new-history-directory> fsck --full --no-reflogs
```

The archive worker was paused only during public pipeline-volume capture and
resumed immediately afterward. Temporary Docker staging held a local workspace
copy to avoid slow Windows bind-mount traversal; that copy was never an upload
asset. Checksums apply to the published ZIP snapshot. Later recovery scripts and
verification receipts are recovered from the working-copy branch, which also
contains the previously uncommitted project files.

Git may repack objects during fetch. For the final scan, native `git cat-file
--batch-all-objects --batch` exported the uncompressed objects into an ignored
private binary file using a binary stream (no console output). The Docker helper
scanned that stream against the current live credentials and private-key patterns.
That stream, original `.git` files and private exclusion details are not release assets.
## Reviewed workspace supplement

`workspace-supplement.zip` contains public benchmark JSON reports, the public
certifi CA bundle, a dummy replacement for a vendored test private-key fixture,
and two SQLite warehouse samples verified to contain only the January 2013
public archive tables/month. The SQLite copies were rebuilt with VACUUM INTO;
private migration databases were not included. This closes conservative initial
exclusions without publishing personal screenshots or study data.

Restore this archive with `--archive workspace-supplement` into another empty
folder, using the same Docker command as the workspace restore. Its internal paths
are relative to the project root. Merge them into the restored workspace only
where the corresponding path is absent; refuse any overwrite. For example:

```powershell
$supplement = (Resolve-Path ../chess-restored-supplement).Path
$workspace = (Resolve-Path ../chess-restored-workspace).Path
Get-ChildItem -LiteralPath $supplement -Recurse -File | ForEach-Object {
  $relative = [IO.Path]::GetRelativePath($supplement, $_.FullName)
  $destination = Join-Path $workspace $relative
  if (Test-Path -LiteralPath $destination) { throw "Refusing overwrite: $relative" }
  New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($destination)) | Out-Null
  Copy-Item -LiteralPath $_.FullName -Destination $destination
}
```

The original excluded-files inventory predates this review. The supplement's
manifest and final coverage receipt identify which paths are now recoverable.
The supplementary review is reproducible with "scripts/review_public_backup.py" inside Docker, mounting the repository read-only at /workspace and the workdir read-write at /receipt. It validates public SQLite table names and January-2013-only source months before VACUUM INTO creates a clean copy. Five focused recovery-helper tests cover corruption, traversal, overwrite refusal, credential replacement and notebook output removal.

Final acceptance: 18 downloaded assets matched the prepared bytes; all 9,046 included files across eight ZIPs were restored and verified in an isolated Docker volume. Both public SQLite warehouses passed integrity checks and had 121,114 fact rows each. A fresh Git clone passed fsck and all 25 original reference mappings. Private PostgreSQL/study state and new-device application acceptance remain outside this verification; deletion safety remains false. The release tag records the initial snapshot; obtain final receipts from the latest working-copy backup branch.
