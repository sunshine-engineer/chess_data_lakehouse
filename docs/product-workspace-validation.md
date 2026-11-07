# Product workspace acceptance

## Outline

1. Verify the public archive and model contracts.
2. Exercise populated and unavailable states in a browser.
3. Inspect the rendered desktop/mobile experience.
4. Keep private study and deployment boundaries explicit.

## Executed local checks

| Check | Result |
|---|---|
| Web/ML and product API tests | 15 passed in disposable PostgreSQL databases |
| Populated browser acceptance | 12 scenarios passed over 121,332 retained archive games and the existing verified model publication |
| Empty-data browser acceptance | 9 scenarios passed with no January publication and no model publication |
| Personal Study regression | 9 synthetic account/import/board/question/export scenarios passed, including delayed upload completion after switching sidebar pages |
| Deep pagination | HTTP cursor continuation after row 10,025 returned 25 additional ordered, non-overlapping games |
| Mobile/keyboard behavior | All five public sections and the case reader fit 390px; skip-link focus and native navigation order checked |
| Failure recovery | Forced dependency failure hides stale values; Retry restores the view; held older filter responses cannot replace a newer selection |
| Evidence validity | Missing, malformed and non-finite benchmark evidence cannot become valid charts; corrupt/stale model publications block estimates |
| Local deployment | All 12 populated scenarios passed again against the deployed localhost:8080 app |
| Private-data preservation | Before/after counts and row fingerprints matched for game revisions, snapshots, memberships, saved studies, review notes and engine evidence; position counts also matched |

The populated preview mounts the existing public archive/model volumes read-only.
Its private-study proxy is disabled. The empty and Personal Study checks use a
separate synthetic QA database and account namespace. No archive download or model
training was started for this delivery.

See [populated acceptance](../reports/product/acceptance.json),
[empty-state acceptance](../reports/product-empty/acceptance.json),
[deep-pagination evidence](../reports/product/deep-pagination.json), and the
[delivery plan](product-workspace-plan.md). The existing Personal Study evidence is
separate from the public archive. Real account details and game libraries are not
included in these public screenshots.

Rendered examples: [archive overview](../reports/product/overview-desktop.png),
[model scenario](../reports/product/model-scenario.png), and
[mobile benchmark view](../reports/product/pipelines-mobile.png).

## Features covered

- Archive overview uses actual publication coverage and provides linked clock,
  result, day and opening selections.
- Explorer keeps filters in shareable URLs, searches source IDs, exposes recorded
  game facts, uses keyset browsing and offers capped CSV with truncation/checksum
  headers. It does not claim that move sequences are stored in the archive facts.
- Benchmark plots, repeat-run details, semantic checks and downloads describe the
  retained measurement group rather than the current serving image.
- Model scenarios combine outcome, length, saved baselines and labelled comparable
  archive observations. All model components use one verified run. The archive
  comparison checks its publication within a consistent read snapshot.
- Five case studies retain application navigation and resolve internal methodology
  links. Public archive pages remain independent from private account libraries.
- Leaving Personal Study stops its page polling and invalidates pending redirects;
  uploads still complete in the background and remain available in Recent imports.

## Operating boundary

This delivery finalizes the local application workflow. Public hosting still
requires deployment authentication, TLS, request limiting and independent review.
Historical January 2013 model results do not establish accuracy on current games.
Local benchmark medians do not establish five-year or 30 GB/month capacity.

The application has no training, ingestion, arbitrary SQL, or arbitrary artifact
upload endpoint. All-month bulk ingestion remains disabled. CSV is intentionally
bounded; each page is snapshot-consistent, while a sequence of pages browses the
current publication and should be refreshed after a source replacement.

For a separate local preview, with the normal database already running:

```bash
make product-preview
make product-test
# Open http://localhost:18083/#overview
```

The preview uses `compose.product-qa.yaml` and cannot proxy private-study writes.
Normal deployment uses the base Compose file and localhost:8080. Review-commit CI
repeats pipeline/Spark recovery, web/product API, empty-state browser, Personal
Study and backup/restore checks; inspect the PR's current checks for its result.
