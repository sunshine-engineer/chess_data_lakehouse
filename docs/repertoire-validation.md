# Repertoire and archive validation

Validated in Docker on 2026-11-08. The private library build is intentionally left running until paused by its owner; it is not reported as a completed 12,533-game scan.

| Check | Result |
| --- | --- |
| Personal study regression suite | 69 passed |
| Full runner regression suite | 61 passed, 9 optional web-module skips; includes PostgreSQL, multiprocessing and Spark |
| Final repertoire / archive / narration integration tests | 7 passed |
| Public app and model tests | 15 passed |
| Archive subprocess, HTTP resume and framed storage tests | 10 passed |
| Final archive scheduling / pause / publication / cursor tests | 4 passed |
| Repertoire browser | Passed with actual Stockfish on an explicitly synthetic missed-mate game; automatic loss review refresh, hidden solutions, grading, saved attempts, phase list, pause, and mobile layout |
| Recent-game browser | 4 scenario groups passed, including retained answers and engine-line replay |
| Existing board / learning browser | 3 scenario groups passed |
| Public-only browser, private API stopped | 9 scenarios passed with no unexpected API failures; archive controls contact the optional private service only after an explicit click |
| Real-library browser | Passed: personal and endgame exercises, Lakshan clock/ending explanation, and position-27 evaluation retained during replay; no JavaScript errors; temporary session revoked |
| Live official catalog | 166 checksum-backed months; newest 2026-10, oldest 2013-01 |
| Container source consistency | 103 Python / SQL / JS / CSS / template files match the built app image |
| Private row preservation | 25,052 game revisions, 8 snapshots, 75,305 memberships, 1 saved study, and 6 existing engine reviews unchanged |
| Backup / restore | 66 tables restored to a disposable database; SQL fingerprints, constraints, permissions, Parquet/private artifacts, and corrupt-backup rejection passed |

The real background build queued all 12,533 games in the active collection. A live checkpoint found 42 completed, 1 running, and 12,490 queued, with 12 opening, 24 middlegame and 3 endgame exercises. Counts continue changing; consult the app or the timestamped aggregate report. A restart requeued the interrupted game without resetting completed tasks or exercises.

The actual Lakshan_1980 game was checked at played position 27. It ends with a recorded loss despite a material advantage, a final recorded own clock of 5 seconds, and no above-threshold finding in its existing complete engine review. The app identifies these facts and the missing ending reason; it does not label the game a proven timeout or invent a tactical blunder.

Archive publication was exercised using a real subprocess and verified synthetic compressed input in a disposable Docker database. Official metadata discovery was checked live. No 31 GB current archive was downloaded for acceptance, and automatic latest imports remain off unless the user enables them. These checks do not prove monthly 31 GB throughput or production scale.

Public evidence: `reports/repertoire/`, `reports/backup-restore.json`. Raw private PGNs, row IDs, sessions, and live-game browser fixtures remain under ignored `data/validation/` and are not committed.
