# Web application and ML validation

## Outline

1. Actual January model evaluation.
2. Container/API tests and browser acceptance.
3. Scope and reproducibility boundaries.

## Actual ML run

Run `20261106T073653Z-ff35205e` executed in the bounded Docker `ml-train` service,
reading the existing `warehouse_python` publication through `chess_reader`.
No archive was downloaded and no source fact was changed. Full source coverage
was 121,332 games; deterministic sampled coverage was 120,000. Training all three
studies took 61.64 seconds locally. This is one observation, not a runtime SLA.

| Split | Dates | Games |
|---|---|---:|
| Training | 31 Dec 2012–18 Jan 2013 | 68,005 |
| Validation | 19–24 Jan 2013 | 23,370 |
| Untouched test | 25–31 Jan 2013 | 28,625 |

The selected logistic model had log loss **0.7314** versus **0.8137** for the
class-prior baseline; accuracy **63.91%** versus **51.03%**. Balanced accuracy was
only **43.84%**, and zero of 943 draws was the model's top predicted class. This
limitation appears in the model card and the laboratory's confusion matrix.

The selected depth-6 forest had MAE **12.3867** full moves versus **12.5981** for
the median baseline; RMSE **15.9581** versus **16.4059**, and R² **0.0392**.
This is limited predictive value, not precise game-length forecasting.

Player clustering used **2,427** eligible profiles. k=3 had the best compared
silhouette (**0.2770**); repeat-seed ARI was **1.0**. Groups primarily reflected
observed time-control preferences. These are descriptive findings on this dataset,
not skill labels or evidence of stable groups across years.

The [raw report](../reports/ml-evaluation.json) includes every candidate score,
confusion matrix, class counts, test slices, group means, publication signature,
code hash and library versions. Model weights remain in the private named volume.
The three standalone model cards interpret the results in more detail:
[outcomes](ml-outcome.md), [length](ml-length.md), [groups](ml-clusters.md).

## Execution checks

- **55 ingestion tests passed** in the pipeline Docker runtime. The separate web/ML
  test module is skipped there because that image intentionally has no scikit-learn.
- **8 web/ML tests passed** in the separate web image. They exercise real PostgreSQL
  reader queries in disposable databases, date splits, baseline metrics, inference
  from saved models, changed-publication rejection, corrupt artifacts, pagination,
  invalid inputs, missing model/report states and documentation routes.
- Dependency validation (`python -m pip check`) passed in the web container.
- Real HTTP calls returned 200 for health, overview, game rows, benchmarks and
  current model metadata; both saved prediction pipelines returned valid outputs.
- **Chromium browser acceptance passed** against the populated app at desktop
  1536×1050 and mobile 390×844. Filters, two-page exploration, benchmark evidence,
  actual predictions and all three case-study links worked. No JavaScript or API
  failures were observed. See the [acceptance report](../reports/browser/acceptance.json),
  [overview screenshot](../reports/browser/overview-desktop.png),
  [mobile screenshot](../reports/browser/overview-mobile.png), and
  [ML laboratory screenshot](../reports/browser/ml-laboratory.png).
- Documentation links resolve and the raw evaluation report's training-code hash
  matches the ML source files in the final worktree.

Browser screenshots were visually inspected. The check establishes local browser
behavior, not independent human acceptance, public-hosted operation or performance
under concurrent traffic. CI also runs web tests and browser checks against its
explicit synthetic/empty-January state; it does not download the real archive or
invent real-source model scores.

See [acceptance requirements](web-ml-plan.md) and [operating commands](web-app.md).

## Boundary

Real data is January 2013 only, including December-started games retained with
their actual dates. Later-month, modern-game, unseen-player and production
generalization remain unvalidated. The app is local and unauthenticated; it uses
the reader role and cannot trigger ingestion or training. All-month processing
remains disabled. Existing historical ingestion benchmarks describe their earlier
runtime, not the separate web/ML container or its training speed.
