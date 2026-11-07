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

Open **http://localhost:8080/#overview** for the archive workspace, or
**http://localhost:8080/#personal** for private game study. Set `WEB_PORT` in `.env` if that loopback port is in
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
| Archive overview | Actual publication coverage, filtered counts/charts, descriptive highlights, linked clocks/results/openings and explicit missing-data coverage |
| Game explorer | Search by source game ID, game-date ordering, cursor browsing through the full result set, recorded-fact detail pages, public-source links and bounded CSV exports |
| Pipeline benchmarks | Time/memory comparison plots, median metrics, individual repeat runs, semantic checks, source provenance and evidence download |
| Model insights | Separate scenario, evaluation and player-profile views; estimates, baselines and comparable archive observations from one verified model run |
| Case studies | Five practical stories spanning personal study, pipeline engineering and the three model applications; shared navigation and linked methodology |

Overview and explorer share publication/month/clock/result/average-rating/date/ECO
filters. Game-ID search and date ordering belong to the explorer. Filter values
are encoded in view URLs: reloading, sharing and browser Back restore the selection.
The copy-view control falls back to the browser address bar when clipboard access
is unavailable. Navigation preserves the archive selection; Reset clears it.
Default
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
| `GET /api/catalog` | Actual public archive publications, counts, date coverage, publication times and UTC check time; no private study data |
| `GET /api/games` | Same filters, `limit` 1–100, date/ID ordering and filter-bound keyset cursor; legacy `offset` remains limited to 0–10,000 |
| `GET /api/games/{id}` | Recorded facts, clock, opening, original source link and publication lineage; no moves or usernames |
| `GET /api/games.csv` | Matching facts and lineage, default 5,000 rows; explicit `export_limit` 1–10,000, bounded disk spooling, checksum and truncation headers |
| `GET /api/benchmarks` | Committed `comparison-v2.json`; explicit unavailable state when missing |
| `GET /api/models` | Verified model report and freshness versus its warehouse publication |
| `POST /api/predictions` | Outcome probabilities, game-length estimate, saved baselines and descriptive comparable-archive observations from one model run |
| `POST /api/predict/outcome` | Win/draw/loss probabilities from the saved logistic pipeline |
| `POST /api/predict/length` | Expected full moves from the saved regression pipeline |
| `GET /api/evidence/{benchmarks,models}` | Download the verified benchmark or model evaluation JSON; no weights or private data |
| `GET /api/case-studies` | Five curated product-story summaries |
| `GET /case-studies/{personal,pipelines,outcome,length,clusters}` | Practical cases and trusted checked-in methodology, rendered with application navigation |
| `GET /guides/{name}` | Allowlisted operating guides, with internal documentation links resolved |

Filter names: `engine` (`python`, `python_mp`, `spark`), `month` (`YYYY-MM`), `speed`,
`min_rating` and `max_rating` (0–4000), `result` (`1-0`, `0-1`, `1/2-1/2`),
`from_date`/`to_date` (inclusive ISO game dates), `eco` (A00–E99), and exact eight-character
`game_id`. Explorer `sort` is `id`, `newest` or `oldest`; a returned `next_cursor`
is passed as `cursor` with the same selection. A changed selection requires a
first-page request. Cursors avoid deep OFFSET scans and have no 10,000-row ceiling.
Each response is internally snapshot-consistent; pages browse the current publication,
not a long-lived database snapshot. Refresh after a new source publication.
SQL values are parameters; schema names come
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

The primary scenario tool calls `POST /api/predictions` once, so outcome, length
and saved baselines carry the same run ID. The archive comparison requires the
same time class and both ratings within 200 points; it is labelled as descriptive
full-archive observations, not a confidence interval or the training sample.
A second publication check inside that read snapshot rejects a source change
during preparation. Missing/corrupt/stale models disable estimates while the
methodology and historical evidence remain accessible. Editing inputs clears the
previous estimate; cancelled or superseded responses cannot replace the active view.

CSV exports are explicitly capped and ordered by game ID. `X-Export-Rows`,
`X-Export-Total`, `X-Export-Truncated` and `X-Content-SHA256` describe the file.
Text beginning with spreadsheet-formula characters is escaped. Temporary files
close after streaming; the full library is never collected into a Python list.

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

The [product acceptance record](product-workspace-validation.md) covers the
finished sidebar flows, populated/empty states, race handling and read-only
preview. `make product-preview` opens that optional preview on localhost:18083;
`make product-test` runs the same browser acceptance against it.

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
search/detail pages, bounded CSV, benchmark evidence, predictions/baselines,
missing/stale models, failure recovery and case-reader links. Screenshots and an acceptance report
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
