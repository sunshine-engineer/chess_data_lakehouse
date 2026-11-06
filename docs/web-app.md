# Chess Atlas: application and ML operations

## Outline

1. Start the existing warehouse, train models and open the app.
2. Explore the UI and read-only API.
3. Understand evaluation, artifacts, recovery and security boundaries.
4. Run Docker tests and reproduce the delivered evidence.

## Setup

Use the repository's existing `.env` and named PostgreSQL data volume. A fresh
checkout must first follow the root README to set three distinct passwords, run
migrations and ingest the one allowed month. Never replace an existing `.env` with
the example: the database retains its original administrator password.

```bash
docker compose up -d --wait postgres
# On a fresh warehouse, after setting .env:
docker compose run --rm migrate
docker compose run --rm python
# Optional explorer warehouse choices:
docker compose run --rm python-mp
docker compose run --rm spark

docker compose --profile web build web
docker compose --profile web run --rm ml-train
docker compose --profile web up -d --wait web
```

Open **http://localhost:8080**. Set `WEB_PORT` in `.env` if that loopback port is in
use. All Python, training, PostgreSQL and browser checks run inside containers.
The UI has no build-time npm dependencies or external font/chart/CDN requests.
It runs with Waitress, not Flask's development server.

Training reads at most 120,000 deterministically selected games and 20,000 eligible
player aggregates. It is capped at two CPUs and 2 GiB; estimator jobs and native
numeric threads are bounded to two. A lock prevents concurrent training jobs.
Only `--month 2013-01` is accepted. An optional `--max-games` must be 1,000–150,000
and adequate date/class coverage must still pass. Training never downloads data.

## Pages

| Page | What it proves or exposes |
|---|---|
| Overview | Real PostgreSQL counts, rating/move averages, game dates, speeds, results, rating bands, top opening families and unknown/missing coverage |
| Game explorer | Filtered facts, 25-row UI pages, ratings, results, game dates and moves; no player usernames |
| Pipeline benchmarks | Historical committed comparison report with run group, sample count, CPU/memory/time and semantic checks; separate from the currently serving image |
| ML laboratory | Three measured models, baselines/comparisons, temporal split counts, outcome confusion matrix, speed slices, group profiles and interactive pre-game predictions |
| Case studies | Three model cards covering question, features, leakage controls, method, metrics, limitations and reproduction |

Overview and explorer share warehouse/month/speed/average-rating filters. Default
rating bounds include missing-rating games; explicit rating bounds exclude them.
Rejected-record count describes the whole selected archive, because rejected rows
do not necessarily have usable speed or rating fields to apply fact filters.
Models and benchmarks have their own fixed source/evaluation provenance and are
not retrained or re-filtered when exploratory filters change. The model label names
its training engine and month. December-started games in the January archive retain
their true game dates. Model training sorts those dates rather than archive labels.

## API

| Endpoint | Contract |
|---|---|
| `GET /healthz` | Database connectivity; does not imply populated data or trained models |
| `GET /api/overview` | One consistent read-only snapshot of filtered aggregate panels |
| `GET /api/games` | Same filters, `limit` 1–100, `offset` 0–10,000; bounded exploration rather than full export |
| `GET /api/benchmarks` | Committed `comparison-v2.json`; explicit unavailable state when missing |
| `GET /api/models` | Verified model report and freshness versus its warehouse publication |
| `POST /api/predict/outcome` | Win/draw/loss probabilities from the saved logistic pipeline |
| `POST /api/predict/length` | Expected full moves from the saved regression pipeline |
| `GET /case-studies/{outcome,length,clusters}` | Checked-in model documentation, rendered locally |

Filter names: `engine` (`python`, `python_mp`, `spark`), `month` (`YYYY-MM`), `speed`,
`min_rating` and `max_rating` (0–4000). SQL values are parameters; schema names come
from an allowlist. Queries time out after eight seconds. There is no arbitrary SQL,
training, shell execution, artifact upload or mutation endpoint. Game IDs, not
player identities, appear in bounded exploration.

Example prediction (shell HTTP request; inference executes inside Docker):

```bash
curl -s http://localhost:8080/api/predict/outcome \
  -H 'Content-Type: application/json' \
  -d '{"white_rating":1600,"black_rating":1500,"initial_seconds":300,"increment_seconds":3}'
```

Only those four finite numeric fields are accepted. Ratings are 0–4000, initial
seconds 0–86,400 and increments 0–3,600. Derived speed matches the ingestion
thresholds. There is no guarantee that every valid input is well represented by
the historical training sample. Bodies are limited to 4 KiB. Invalid input gets
400; stale model predictions get 409; unavailable/corrupt models or database
dependencies get 503 without connection details or tracebacks in responses.

## ML evidence and artifacts

The supervised models use distinct chronological days for train/validation/test.
Preprocessing is fitted in pipelines and hyperparameters use validation only.
After selection, refit on train plus validation before scoring untouched test days.
The clustering case is explicitly retrospective and has silhouette/inertia/seed
comparisons instead of a supervised holdout claim. Read the separate
[outcome](ml-outcome.md), [length](ml-length.md), and [clustering](ml-clusters.md)
model cards and [executed evidence](web-ml-validation.md).

`ml-data` holds immutable run directories with three pipelines, a measured report
and SHA-256 manifest. `latest.json` is replaced atomically only after a complete
run. A failed training job leaves the prior publication usable; a partial hidden
directory never becomes current. The application mounts this volume read-only.
`reports/ml-evaluation.json` is the shareable report, not the model weights.
Each report includes dependency versions, code hash, publication signature and
sampling/split metadata. Both the API and training use the same feature builder.

Changing source publication makes previous models stale; browsing historical
results remains available, but inference requires explicit retraining. Missing or
corrupt artifacts also require retraining. Version mismatches fail before loading.
Checksums detect damage, not a malicious origin: Joblib uses pickle and **must only
load this project's trusted training outputs**. Never copy downloaded/untrusted
model files into the named volume. No artifact upload endpoint is provided.

Model weights live in their own named volume, outside the existing SQL/Parquet
backup bundle. Rebuild them from the retained source and locked runtime; separately
back up `ml-data` if retaining a particular model version is required. Keep the
database/publication and artifact report together when reproducing a result.

## Security and scope

The app and training connect as `chess_reader`, with read-only transactions. They
receive no administrator or loader password. The web process runs as UID 10001,
with a read-only root filesystem, dropped capabilities and a 1 GiB memory cap.
Only a one-shot initializer changes ownership of the model volume. The port binds
to `127.0.0.1`; this is an unauthenticated local portfolio app. Public hosting
requires authentication, TLS, deployment secrets, request limiting and independent
review. Sites/Cloudflare hosting cannot directly reach this local Docker PostgreSQL
TCP service, so this feature uses the repository's Docker deployment.

Application errors have visible UI states. Assets are local; CSP restricts scripts
and connections to the same origin. DOM data is inserted as text; rendered Markdown
comes only from checked-in trusted documentation. The app has no auto-ingestion,
all-month execution or on-request model fitting.

## Tests and teardown

```bash
docker compose --profile web run --rm web-tests
docker compose --profile web build browser-tests
docker compose --profile web run --rm browser-tests
# Existing pipeline regression suite, with its separate runtime:
docker compose build python
docker compose run --rm tests
```

Web tests create/drop disposable databases and leave the real warehouse intact.
Browser checks use the running populated app: desktop/mobile, filters, pagination,
benchmarks, predictions and model-card links. Screenshots and an acceptance report
go to `reports/browser/`. CI uses synthetic/empty application states, not fabricated
real-month model scores. Local real-data results are documented separately.

Stop web services while keeping all data:

```bash
docker compose --profile web stop web
```

For the whole project, enable all profiles when stopping services. Omit `--volumes`
to preserve warehouses, model versions, source data and backups:

```bash
docker compose --profile tools --profile cluster --profile web down
```

## References

- [Flask deployment guidance](https://flask.palletsprojects.com/en/stable/deploying/)
- [scikit-learn model persistence and trust](https://scikit-learn.org/stable/model_persistence.html)
