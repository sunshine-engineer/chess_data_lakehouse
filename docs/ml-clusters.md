# Case study: describing observed player groups

## Outline

1. Descriptive question and aggregation grain.
2. Feature scaling, cluster comparison and stability.
3. Report interpretation and reproduction.
4. Limitations and next experiment.

## Question and grain

What patterns appear among players observed repeatedly in this archive? This is
retrospective unsupervised analysis, not an outcome predictor, skill ranking,
coaching recommendation, personality assessment or cheating detector.

Expand each January game into one observation for each color. Exclude the reserved
unknown identity. Require at least ten observed games and five available rating
observations per player. Limit to 20,000 eligible players by a deterministic hash
of their natural key. Aggregate in PostgreSQL; export only anonymous numeric
profiles. No usernames, hashes, per-player assignments or lookup API are published.

Features are mean available rating, mean game moves, win rate, draw rate and shares
of bullet (including ultrabullet), blitz and rapid games. Classical and unknown
time classes form the remaining share. These post-game aggregate features are
appropriate for description; they are not pre-game predictors.

## Model and comparisons

Standardize the seven features before K-means so rating scale cannot dominate.
Compare k=2, 3 and 4 with ten initializations and seed 42. Choose the largest
silhouette score using a deterministic sample of at most 2,000 profiles. Also
report candidate inertia and a one-cluster reference inertia. The one-cluster
reference has no meaningful silhouette score; it is not a labeled baseline.

Repeat the selected k with seed 43 and report adjusted Rand index (ARI) between
the two assignments. This checks initialization sensitivity on this dataset,
not stability across new months. Silhouette favors some geometric structures;
selection is exploratory and no held-out predictive accuracy is claimed.

Report cluster counts and mean profiles in original units. Cluster IDs are
arbitrary labels with no order. Scaled centers and retrospective class shares
can reflect the source's rating/time-control mix rather than persistent traits.

## Measured evidence and reproduction

Current run `20261106T134842Z-4c1ec831` included **2,427 eligible players**.

| Candidate k | Silhouette | Inertia |
|---|---:|---:|
| 2 | 0.2446 | 12,957.67 |
| 3 (selected) | 0.2770 | 10,450.39 |
| 4 | 0.2580 | 8,710.46 |

The selected groups contained 799, 1,265 and 363 profiles. Their dominant observed
time classes were blitz, rapid and bullet, respectively. Seed-repeat ARI was 1.0,
but silhouette was only 0.277: repeatable initialization does not establish sharply
separated natural categories. The one-cluster inertia reference was 16,989.00.

`reports/ml-evaluation.json` under `clusters` records eligibility count, k
comparisons, silhouette, inertia, seed stability and aggregate profiles. The ML
laboratory shows those measured profiles. See `docs/web-ml-validation.md` for the
actual run interpretation.

```bash
docker compose --profile web build web
docker compose --profile web run --rm ml-train
docker compose --profile web up -d --wait web
```

Open `http://localhost:8080/#models`. Training also persists the clustering pipeline
for reproducibility, but the app exposes aggregate profiles rather than individual
player classification. No model training or source download occurs on a web request.

## Limits and next experiment

Eligibility excludes occasional players, unknown identities and sparsely rated
profiles, so the clusters do not describe every Lichess user. The pipeline observes
only one archive month, and incomplete history affects rates. Seed agreement is
not validity of the interpretation. Next: compare later approved months, bootstrap
profile stability and alternative cluster geometries before assigning human labels.

## References

- [scikit-learn: silhouette score](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.silhouette_score.html)
- [scikit-learn: adjusted Rand index](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.adjusted_rand_score.html)
