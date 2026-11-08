# Portable Kubernetes deployment and cutover

Outline: choose an overlay, configure storage/secrets/images, transfer state,
validate the offline destination, then enable traffic and workers.

This setup is prepared for a cluster you choose later. It uses standard Kubernetes
resources and [Kustomize overlays](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/).
Local validation renders manifests and checks Kubernetes 1.36 schemas; it does not
prove a real cluster's storage, scheduling, admission policies, DNS, ingress or TLS.

| Package | Purpose |
|---|---|
| `deploy/k8s/base` | Namespace, ConfigMap, PVCs, Services, four application Deployments, ingress NetworkPolicy |
| `overlays/external` | External PostgreSQL and placeholder RWX storage class; copy and customize |
| `overlays/dev` | Single-node development PostgreSQL StatefulSet and RWO storage; no HA |
| `jobs/bootstrap` | Additive SQL migrations and database-role password provisioning |
| `jobs/transfer` | Explicit maintenance-window database restore; manual resume, persistent tracker |
| `jobs/volume-access` | Temporary artifact-volume copy/checksum helper Pod |
| `jobs/pipeline`, `jobs/train` | Opt-in archive processing and model training |

All four application Deployments initially have **zero replicas**. Applying the
base cannot start application writers before database and file transfers finish.
Kubernetes does not order independent Deployments; the cutover sequence below is
intentional. Migration/bootstrap Jobs are separate from the base.

## Prepare a cluster overlay

Copy an overlay within `deploy/k8s/overlays/` so its `../../base` reference stays
valid. Configure these before applying it:

1. Push the runner, web and migrator images to your registry; set Kustomize `images`
   entries for each image in your deployment **and each separate job overlay**.
   Use reviewed digests. `:local` tags are development placeholders. The current
   Dockerfiles and ops downloads target Linux amd64; ARM builds need adjustment.
2. Patch `chess-config`: PostgreSQL host/port/database/admin role, SSL mode and CA
   paths. External defaults require `verify-full`. Match the server name to its TLS
   certificate. Cloud authentication helpers are provider-specific and not bundled.
3. Select a storage class, volume capacities and backup policy. Base artifact PVCs
   use RWX because API and worker Pods share files. Verify **POSIX flock and atomic
   rename/fsync semantics** on the selected storage. Object-storage mounts do not
   automatically satisfy these contracts. Development RWO claims work only when
   all consumers can mount them on the same node. Check your CSI driver's limits.
4. Supply Secret `chess-credentials` with keys `admin-password`, `loader-password`,
   `reader-password`, `study-password`, `study-api-token`. Workloads mount only the
   selected keys needed for their role. External TLS uses Secret `chess-db-tls`
   containing `ca.crt`. Transfer additionally uses `chess-source-credentials`
   (`source-password`) and `chess-source-db-tls` (`ca.crt`). Store real files outside
   the repository and create Secrets from files; never paste them into YAML.
5. Choose your ingress controller, hostname and TLS certificate. Services are
   private ClusterIP. Secure cookies require HTTPS. No provider-specific ingress,
   storage provisioner, certificate manager or load balancer is installed here.

Example image override in each relevant `kustomization.yaml`:

```yaml
images:
  - name: chess-lakehouse-web
    newName: registry.example/chess-web
    digest: sha256:REPLACE_WITH_VERIFIED_DIGEST
```

Pods run as UID/GID 10001, drop capabilities, disable service-account tokens, use
read-only root filesystems and have explicit resources and probes. The dev database
uses UID/GID 999. The namespace requests restricted Pod Security. NetworkPolicy
limits inbound traffic to same-namespace clients and web port 8080; it does **not**
implement an egress allowlist or protect the database from other authorized
namespace workloads. Effective network enforcement requires a supporting CNI.

Keep study-worker and archive-worker at **one replica** with Recreate strategy.
The queues are durable in PostgreSQL, but filesystem ownership locks assume one
worker per shared root; this setup does not claim multi-worker HA. API workers
are disabled with `STUDY_WORKER=0`. `/livez` avoids database checks; `/readyz`
checks dependencies. Database outages should remove readiness without repeated
liveness restarts. Study workers drain current work during SIGTERM grace; a forced
import stop leaves published games intact and the interrupted job retryable.

## Containerized kubectl

Build and inspect without touching a cluster:

```bash
docker build -f docker/ops.Dockerfile -t chess-lakehouse-ops:local .
docker run --rm -v "$PWD:/workspace:ro" chess-lakehouse-ops:local
```

For cluster work, mount a kubeconfig whose endpoint is reachable from Docker.
An embedded `localhost` control-plane URL generally needs adjustment. Use a
provider-supplied authenticated kubectl image if the kubeconfig requires an exec
plugin absent from this image. The following helper keeps kubectl inside Docker:

```bash
export KUBECONFIG=/absolute/path/to/your/kubeconfig
k() {
  docker run --rm -i --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e KUBECONFIG=/kubeconfig \
    -v "$KUBECONFIG:/kubeconfig:ro" -v "$PWD:/workspace:ro" \
    -v /absolute/path/to/chess-credentials:/credentials:ro \
    --entrypoint kubectl chess-lakehouse-ops:local "$@"
}
k config current-context
k kustomize deploy/k8s/overlays/your-cluster
# Inspect output, context and namespace before applying anything.
```

The credential directory above should contain files named for each Secret key.
Create the namespace first, then supply secrets and TLS CAs, then apply your overlay.
For example, after namespace creation:

```bash
k -n chess-lakehouse create secret generic chess-credentials \
  --from-file=admin-password=/credentials/admin-password \
  --from-file=loader-password=/credentials/loader-password \
  --from-file=reader-password=/credentials/reader-password \
  --from-file=study-password=/credentials/study-password \
  --from-file=study-api-token=/credentials/study-api-token
```

Secret storage encryption, RBAC, rotation and cluster backups are responsibilities
of the chosen cluster. Kubernetes Secret objects alone are not a vault.

## Maintenance-window sequence

1. Confirm a recent restorable backup. Stop **all** source writers: API/embedded
   worker, standalone study worker if used, archive worker, scheduled imports,
   training/pipeline jobs, notebooks and outside clients. Stop destination writers
   too. Verify no running jobs/clients continue modifying files or PostgreSQL.
   This preparation has not stopped the existing live repertoire build.
2. Apply your configured base/overlay with app replicas still zero. Confirm PVCs
   bind and PostgreSQL is reachable. For an existing source, run database transfer
   **before bootstrap**: bootstrap creates tables and would make the destination
   nonempty. Configure the transfer job's source endpoint and credentials; apply
   its configured overlay and wait for successful completion/log report. Resume a
   failed Job by deleting only that Job and recreating it with the **same PVC and
   `/migration/main` path**. Never delete the PVC or source database to retry.
3. Copy all durable file volumes while source writers remain stopped. PostgreSQL
   transfer does not include these files:

   | Container path | Source Compose state | Destination PVC |
   |---|---|---|
   | `/study` | `chess-lakehouse_study-data` | `study-data` |
   | `/data` | `chess-lakehouse_pipeline-data` | `pipeline-data` |
   | `/models` | `chess-lakehouse_ml-data` | `model-data` |
   | `/reports` | Local `reports/` bind directory | `report-data` |

   Prefer your CSI snapshot/restore or reviewed bulk transfer mechanism at scale.
   Preserve paths, file contents, directory structure and UID/GID access. The
   temporary helper mounts all four volumes and `/migration` for manifests.
   Generate source hashes with `python -m chess_lakehouse.volumes inventory` in a
   runner container mounting the four source roots; save its stdout **outside**
   those roots. Transfer the roots to empty PVCs and the manifest to
   `/migration/artifact-manifest.json`. Then run:

   ```bash
   k -n chess-lakehouse exec chess-volume-access -- \
     python -m chess_lakehouse.volumes verify \
     --manifest /migration/artifact-manifest.json
   ```

   The check hashes every regular file in bounded blocks and rejects symlinks,
   missing/extra/changed files. Transient `.lock` files are excluded. It validates
   contents, not filesystem ACLs or storage-driver behavior. Do not copy raw
   PostgreSQL data directories between independent instances. Keep the original
   source volumes and backups until cutover acceptance.
4. Run bootstrap with destination credentials to provision/rotate application
   passwords and apply compatible additive migrations. This changes role passwords
   cluster-wide: shared-role clusters require coordination. For a **fresh empty
   install without a source transfer**, run bootstrap directly instead.
5. Start destination API and web at one replica. Check readiness, sign-in, library
   counts, retained snapshots, source-game links, PGN/Parquet export, model loading
   and archive publication queries. Start study-worker and archive-worker at one
   replica only after those checks. Confirm queue recovery and a bounded review
   finishes. Remove the temporary volume-access Pod. Keep source writers stopped.
6. Switch ingress/DNS/client traffic and monitor errors, disk, database connections,
   queue age and completed jobs. Retain migration artifacts/report and operator
   acceptance evidence. Scale only the stateless services after separate load tests.

## Rollback boundary

Before destination writes begin, stop destination Pods and return traffic to the
untouched source, then restart source writers. After new destination writes exist,
switching back loses those writes unless you reconcile them: stop both sides and
plan a new controlled transfer/recovery. Never run both sides as independent live
writers against divergent libraries. Keeping a source backup is necessary but does
not by itself provide automatic bidirectional rollback.

Remaining production work depends on the target: ingress/TLS acceptance, actual
storage and recovery tests, database HA/PITR, metrics/alerts, secret management,
capacity/load testing and operational ownership. The supplied Spark job uses
bounded local Spark within one Pod; distributed Spark-on-Kubernetes requires its
own executor, identity, storage and scheduling design.

## Example file-volume copy

These commands are for the future maintenance window, after stopping source
writers and confirming the destination PVCs are empty. Replace Compose volume
names if your project name differs. Use an image built from this revision.
For large archives, use a storage-native copy instead of streaming through kubectl.

```bash
mkdir -p data/transfers
docker run --rm --read-only \
  -v chess-lakehouse_study-data:/study:ro \
  -v chess-lakehouse_pipeline-data:/data:ro \
  -v chess-lakehouse_ml-data:/models:ro \
  -v "$PWD/reports:/reports:ro" \
  --entrypoint python chess-lakehouse-runner:local \
  -m chess_lakehouse.volumes inventory > data/transfers/artifact-manifest.json

# Apply your image-configured volume-access job overlay before this copy.
# Bash pipefail makes a failed source export fail the combined command.
set -o pipefail
docker run --rm --read-only \
  -v chess-lakehouse_study-data:/study:ro \
  -v chess-lakehouse_pipeline-data:/data:ro \
  -v chess-lakehouse_ml-data:/models:ro \
  -v "$PWD/reports:/reports:ro" \
  --entrypoint tar chess-lakehouse-runner:local -C / -cf - study data models reports \
  | k -n chess-lakehouse exec -i chess-volume-access -- \
      tar -C / -xf - --no-same-owner --no-same-permissions --no-overwrite-dir
k -n chess-lakehouse exec -i chess-volume-access -- \
  sh -c 'cat > /migration/artifact-manifest.json' < data/transfers/artifact-manifest.json
k -n chess-lakehouse exec chess-volume-access -- \
  python -m chess_lakehouse.volumes verify --manifest /migration/artifact-manifest.json
```

The tar stream has no per-file resume ledger. A failed copy requires rerunning it
against the same offline destination and frozen source, then verifying the entire
manifest. Do not point these commands at a destination with independent data.
