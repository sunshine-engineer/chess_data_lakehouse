# Case study: predicting game outcomes

## Outline

1. Question and prediction boundary.
2. Dataset, features and chronological evaluation.
3. Baseline, model, measured results and limitations.
4. Reproduction and operational use.

## Question

Can ratings and the clock improve a win/draw/loss probability estimate over the
historical class proportions? The target is the final result: white win (`1-0`),
black win (`0-1`) or draw (`1/2-1/2`). Prediction happens before the first move.
This is a statistical portfolio experiment, not a chess engine or betting tool.

## Dataset and features

Read January 2013 facts and time controls from `warehouse_python` using
`chess_reader`. Keep at most 120,000 games by a deterministic hash of game ID;
sampling does not inspect targets. Record full publication signature, sampled
rows, feature contract, dependency versions, code hash and seed in the report.

Features are both pre-game ratings, their difference and mean, initial seconds,
increment seconds, and fixed one-hot time classes. Missing numeric values use
training medians. The archive's game-start date is used only for splitting.

Exclude results, winner, moves, termination, opening inferred from moves, player
identifiers, and current player titles. Those would leak the target, introduce
post-game information, or encourage memorization. Ratings are assumed to describe
the pre-game header; external rating histories are not reconstructed.

## Evaluation

Split distinct sorted game dates into approximately 60% training, 20% validation,
20% test days. No date crosses a boundary. Require at least ten dates, 100 games in
each split, and all three classes in every split. Preprocessing lives inside the
estimator pipeline. Tune logistic regression `C` over 0.1 and 1.0 using validation
log loss, refit on training plus validation, then evaluate the final test days once.

Compare with a `DummyClassifier(strategy='prior')` fitted on the same refit data.
Report log loss, accuracy, balanced accuracy, macro F1, class counts, confusion
matrix and time-class test slices. Lower log loss is better; accuracy can conceal
weak draw performance in this imbalanced dataset. Coefficients describe association
and correlated features make individual coefficient interpretations unstable.

Measured results are in `reports/ml-evaluation.json` under `outcome`, and the app's
ML laboratory displays them alongside the baseline. A missing report is an
untrained state, never a fabricated score. See `docs/web-ml-validation.md` for the
executed run and measured interpretation.

## Reproduce

The executed run `20261106T073653Z-ff35205e` used 68,005 training, 23,370 validation
and 28,625 test games. Test dates were 25–31 January 2013; selected `C=1.0`.

| Untouched test metric | Logistic model | Class-prior baseline |
|---|---:|---:|
| Log loss | 0.7314 | 0.8137 |
| Accuracy | 63.91% | 51.03% |
| Balanced accuracy | 43.84% | 33.33% |
| Macro F1 | 0.4311 | 0.2253 |

**Critical finding:** none of the 943 actual test draws was predicted as the most
likely class. Improved accuracy and log loss do not make this a strong draw
classifier. Preserve the full confusion matrix and investigate this weakness in
future experiments rather than presenting overall accuracy alone.

```bash
docker compose --profile web build web
docker compose --profile web run --rm ml-train
docker compose --profile web up -d --wait web
```

Open the ML laboratory at `http://localhost:8080/#models`. Both training and serving
use `ml/features.py`; `/api/predict/outcome` accepts only ratings and clock values.
See `docs/web-app.md` for setup, API examples and artifact recovery.

## Limits and next experiment

This one-month historical holdout does not establish modern-game accuracy,
unseen-player generalization or calibrated probabilities. Players can recur across
dates. There is no confidence interval or cross-season validation claim. Next,
evaluate later explicitly approved months, player-disjoint splits, calibration
curves and uncertainty by rating band before changing the generalization claim.
The UI labels all predictions as historical estimates.

## References

- [scikit-learn: preventing leakage with pipelines](https://scikit-learn.org/stable/common_pitfalls.html)
- [scikit-learn: log loss](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.log_loss.html)
