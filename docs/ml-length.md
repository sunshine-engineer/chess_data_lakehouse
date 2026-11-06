# Case study: estimating game length

## Outline

1. Question, target and available features.
2. Model and baseline.
3. Chronological evaluation and diagnostic evidence.
4. Reproduction, limitations and next experiment.

## Question and target

Can the pre-game ratings and clock estimate the eventual full-move count better
than always predicting the historical median? The target is `move_count`, defined
by the ingestion model as the mainline ply count rounded up to full moves. It is
not elapsed wall-clock time, thinking time or remaining time.

The experiment reads the same deterministic January sample and uses the same
chronological train/validation/test day boundaries as outcome classification.
Features are ratings, difference, average, initial seconds, increment and one-hot
speed. Move count, ply count, result, termination, opening and player identity are
excluded from predictors. Missing numeric features use fitted training medians.

## Model and baseline

Use a random forest regressor with 60 trees, at least 20 observations per leaf,
two CPU jobs and seed 42. Compare maximum depth 6 versus 10 by validation MAE.
Refit the selected pipeline on training plus validation and evaluate once on the
untouched last dates. The baseline predicts the refit set's median move count.

Report mean absolute error in moves (MAE), root mean squared error (RMSE), R²,
validation candidate scores, time-class test slices and forest feature importance.
Lower MAE/RMSE is better. Negative R² is possible; report it as observed. Feature
importance indicates model usage and is not causal evidence or a reliability score.

The actual measured values are stored in `reports/ml-evaluation.json` under
`length` and displayed next to the median baseline in the app. The executed run is
interpreted in `docs/web-ml-validation.md`. A model that fails to beat the baseline
is still a documented finding, not a reason to hide the result.

## Reproduce and inspect

Executed run `20261106T073653Z-ff35205e` selected maximum depth 6 using validation
data. Its 28,625 test games came from 25–31 January 2013.

| Untouched test metric | Random forest | Median baseline |
|---|---:|---:|
| MAE, moves | 12.3867 | 12.5981 |
| RMSE, moves | 15.9581 | 16.4059 |
| R² | 0.0392 | -0.0155 |

This is a small improvement, approximately 0.21 moves in MAE. Pre-game metadata
explains little of the observed length variation. The correct case-study conclusion
is limited predictive value, not high precision.

```bash
docker compose --profile web build web
docker compose --profile web run --rm ml-train
docker compose --profile web up -d --wait web
```

Open `http://localhost:8080/#models`. The shared prediction form calls
`POST /api/predict/length` with pre-game ratings and clock values. It reports an
expected move count, not an exact duration or interval guarantee. The app cannot
start training. See `docs/web-app.md` for API and model storage details.

## Limits and next experiment

Resignations, quick draws, disconnections and unequal skills make game length
uncertain before play. One historical month and repeated players constrain the
evidence. A date split is stronger than shuffled evaluation for this use case but
does not establish behavior across years. This model does not use board positions
or engine evaluation. Next experiments: later-month evaluation, quantile models,
residual diagnostics by rating band, and independently evaluated prediction
intervals. Do not present the current point estimate as a guaranteed range.

## References

- [scikit-learn: random forest regression](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html)
- [scikit-learn: common leakage pitfalls](https://scikit-learn.org/stable/common_pitfalls.html)
