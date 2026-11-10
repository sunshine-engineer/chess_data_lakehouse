# Chess Game Analytics Lakehouse on GCP

## Summary

Build a batch data lakehouse that analyzes approximately **2.2 million Lichess games** from January–March 2014.

The finished pipeline will demonstrate:

- Python ingestion and compressed-data handling
- Cloud Storage data-lake design
- PySpark transformations on serverless Spark
- Dimensional modeling
- BigQuery partitioning and clustering
- Data-quality controls and rejected-record handling
- Terraform, IAM, CI, orchestration, and cost controls
- A Looker Studio analytics dashboard

Lichess publishes its database under CC0. The selected three files total about 353 MB compressed and contain roughly 2.2 million games—large enough to justify Spark while remaining practical for a three-day build. [Lichess database](https://database.lichess.org/)

## Architecture and Data Flow

```text
Lichess monthly PGN archives
           |
           v
Python streaming ingestion
           |
           v
Cloud Storage Bronze
raw .pgn.zst + framed PGN JSONL
           |
           v
Managed Service for Apache Spark
parse + validate + deduplicate + enrich
           |
           +--------------------+
           |                    |
           v                    v
Cloud Storage Silver      Rejected records
partitioned Parquet       with rejection reason
           |
           v
BigQuery staging tables
           |
           v
BigQuery dimensional model
           |
           v
Looker Studio dashboard
```

### Processing example

Input PGN:

```text
[Site "https://lichess.org/abc123"]
[UTCDate "2014.01.15"]
[WhiteElo "1650"]
[BlackElo "1780"]
[ECO "B20"]
[Opening "Sicilian Defense"]
[TimeControl "300+0"]
[Result "0-1"]

1. e4 c5 2. Nf3 d6 ...
```

Spark produces a game fact similar to:

```json
{
  "game_id": "abc123",
  "game_month": "2014-01-01",
  "white_rating": 1650,
  "black_rating": 1780,
  "average_rating": 1715,
  "white_rating_band": "1600-1799",
  "black_rating_band": "1600-1799",
  "opening_key": "B20",
  "speed": "blitz",
  "winner_color": "black",
  "move_count": 42,
  "source_month": "2014-01"
}
```

The ingestion program only decompresses and frames complete PGN games into JSONL records. PySpark performs schema parsing, business transformations, validation, enrichment, deduplication, and warehouse preparation.

## GCP Services

| Service | Purpose | Why use it |
|---|---|---|
| Cloud Storage | Bronze and silver lake layers | Native distributed storage for Spark using `gs://` paths |
| Managed Service for Apache Spark | PySpark batch processing | Runs Spark without maintaining a cluster and charges only while workloads execute |
| BigQuery | Analytics warehouse | Demonstrates warehouse modeling, SQL, partitioning, clustering, and query optimization |
| Cloud Workflows | Pipeline orchestration | Runs ingestion, Spark, warehouse loading, and validation in sequence |
| Cloud Logging | Spark and workflow logs | Provides operational evidence and failure diagnostics |
| Looker Studio | Dashboard | Produces a shareable visual portfolio demonstration |
| IAM service accounts | Least-privilege access | Demonstrates cloud security fundamentals |
| Cloud Billing budget | Cost alerts | Warns when project spending crosses configured thresholds |
| Terraform | Infrastructure provisioning | Makes buckets, datasets, IAM, APIs, and workflows reproducible |

Managed Spark reads and writes Cloud Storage natively, and supported runtimes include the BigQuery connector. [GCP Spark data-source documentation](https://docs.cloud.google.com/managed-spark/docs/guides/connect-to-data-sources)

Do not use Composer, GKE, Cloud SQL, Pub/Sub, or Dataflow in this version. They add cost and setup work without strengthening the required Spark batch-pipeline story.

## Storage and Data Model

### Cloud Storage zones

```text
gs://<project>-chess-lake/
├── bronze/
│   ├── archives/source_month=2014-01/
│   └── framed/source_month=2014-01/
├── silver/
│   └── games/source_month=2014-01/
├── rejects/
│   └── source_month=2014-01/
├── spark/
└── temporary/
```

Silver data uses Parquet and is partitioned by `source_month`. Temporary Spark and connector files receive a seven-day lifecycle deletion rule. [Cloud Storage lifecycle management](https://docs.cloud.google.com/storage/docs/lifecycle)

### BigQuery star schema

**`fact_game` — one row per game**

- `game_id`
- `date_key`
- `white_player_key`
- `black_player_key`
- `opening_key`
- `time_control_key`
- White and black ratings
- Rating bands and rating difference
- Result and winner colour
- Move count
- Termination type
- Source month and ingestion timestamp

**`dim_date`**

- Date, month, quarter, year and weekday

**`dim_player`**

- Deterministic SHA-256 player key
- Public username excluded from the warehouse
- Latest observed title

**`dim_opening`**

- ECO code
- Opening family
- Opening variation

**`dim_time_control`**

- Initial seconds
- Increment seconds
- Derived speed category

Partition `fact_game` monthly using `game_month`. Cluster it by `opening_key`, `speed`, and `white_rating_band`. Monthly partitioning avoids creating many tiny daily partitions while still showing partition pruning and cost-aware design. [BigQuery partitioning](https://docs.cloud.google.com/bigquery/docs/partitioned-tables), [BigQuery clustering](https://docs.cloud.google.com/bigquery/docs/clustered-tables)

Spark writes staging tables. BigQuery SQL then:

1. Merges deterministic dimension keys.
2. Deletes existing fact rows for the incoming source months.
3. Inserts the refreshed source-month data.
4. Runs referential-integrity and reconciliation checks.

This makes reruns idempotent.

## Analytics Demonstration

The dashboard should answer five questions:

- Which openings are most popular by rating band?
- Which openings have the highest white, black, and draw rates?
- How does game length vary by speed category?
- How often does the lower-rated player win?
- How did opening popularity change across the three months?

Include:

- KPI cards for games, players, openings, draw rate, and upset rate
- Opening popularity bar chart
- Result distribution by rating band
- Average move count by speed
- Monthly opening trend
- Filters for month, opening, speed, and rating band

## Data Quality and Failure Handling

Validate:

- Unique and non-null `game_id`
- Results limited to `1-0`, `0-1`, or `1/2-1/2`
- Valid game dates
- Ratings within a configurable plausible range
- Non-negative move counts
- Valid time-control parsing
- Fact-to-dimension referential integrity
- Input, accepted, rejected, and output row-count reconciliation
- Minimum expected volume for each source month

Malformed games go to a reject Parquet dataset containing:

- Source month
- Raw game identifier when available
- Failure stage
- Rejection reason
- Ingestion timestamp

Local tests use a small committed fixture, so reviewers can verify transformations without having a GCP account.

## Public Interfaces

Provide these commands through a Makefile:

```bash
make setup
make test
make terraform-plan
make terraform-apply
make ingest MONTHS=2014-01,2014-02,2014-03
make submit MONTHS=2014-01,2014-02,2014-03
make quality
make destroy
```

Configuration comes from environment variables:

```text
GCP_PROJECT_ID
GCP_REGION
GCS_BUCKET
BIGQUERY_DATASET
SOURCE_MONTHS
```

No service-account keys or credentials are committed. Local development uses Application Default Credentials; cloud jobs use a dedicated service account.

## Three-Day Delivery Plan

### Day 1 — Infrastructure and ingestion

- Create a separate GitHub repository named `gcp-chess-analytics-lakehouse`.
- Provision APIs, bucket, BigQuery dataset, service account, IAM, and lifecycle rules with Terraform.
- Implement streaming download/decompression and PGN framing.
- Upload January–March 2014 archives and framed records.
- Commit a small synthetic/sample PGN fixture for local tests.

### Day 2 — Spark and warehouse

- Implement Spark schemas, parsing, deduplication, derived columns, and rejected records.
- Write partitioned silver Parquet.
- Create dimensions, facts, staging tables, and BigQuery merge SQL.
- Add unit tests and data-quality checks.
- Run the complete pipeline and record row counts and runtime.

### Day 3 — Orchestration and presentation

- Add a Cloud Workflow for Spark submission and validation.
- Build the Looker Studio dashboard.
- Add GitHub Actions for Python tests, Ruff, Terraform formatting, and validation.
- Capture cloud screenshots, query results, Spark runtime, and actual cost.
- Complete the README, architecture diagram, data model, limitations, and cleanup instructions.
- Destroy billable processing resources after collecting evidence.

## GitHub Showcase

The repository should contain:

```text
gcp-chess-analytics-lakehouse/
├── infrastructure/
├── src/ingestion/
├── src/spark/
├── sql/
├── workflows/
├── tests/
├── sample_data/
├── docs/
├── .github/workflows/
├── Makefile
└── README.md
```

The README should lead with results:

```text
Processed 2.2M Lichess games through a reproducible GCP lakehouse
pipeline using Cloud Storage, serverless PySpark, and BigQuery.
```

Show these artifacts prominently:

- Mermaid architecture diagram
- Star-schema diagram
- Bronze-to-silver-to-gold explanation
- Spark job screenshot and runtime
- BigQuery tables and partition details
- Data-quality report with accepted/rejected counts
- Looker Studio dashboard screenshots
- Terraform plan summary
- GitHub Actions result
- Actual GCP cost for the demonstration
- Teardown confirmation
- Trade-offs and known limitations

Do not commit bulk Lichess data, generated Parquet, credentials, Terraform state, or screenshots containing project numbers or sensitive identifiers.

## Test and Acceptance Criteria

The project is complete when:

- All three source archives are downloaded with checksum or size validation.
- At least two million games are processed successfully.
- Re-running a source month does not duplicate facts.
- Silver output is valid partitioned Parquet.
- BigQuery facts resolve to every required dimension.
- Invalid fixture records reach the reject dataset with reasons.
- Analytics queries use partition filters and return documented results.
- Local unit tests and CI pass.
- Terraform can provision and tear down the environment.
- README instructions reproduce the pipeline from an empty GCP project.
- The repository contains genuine screenshots and measurements rather than unverified claims.

## Cost Defaults

- Configure a project-level budget alert at **$20**, with notifications at 50%, 80%, and 100%.
- Use one region for Storage, Spark, and BigQuery.
- Run serverless Spark only for development and final evidence runs.
- Delete temporary objects after seven days.
- Do not repeatedly process current 30 GB monthly archives.
- Record actual billing data in the README instead of claiming an estimated cost.

A GCP budget provides alerts and does not automatically cap spending. [Cloud Billing budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets)

## Assumptions

- This is a separate data-engineering repository, not an extension of the existing Kubernetes project.
- The first release is a batch pipeline; streaming is explicitly out of scope.
- January–March 2014 is the default dataset.
- Player names are hashed before the silver and warehouse layers.
- Only game-level facts are created; a move-level fact table is deferred because it could expand the data by more than 100 times.
- Infrastructure is destroyed after evidence is captured, while source code and sanitized screenshots remain on GitHub.
