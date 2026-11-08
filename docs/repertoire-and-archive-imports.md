# Personal repertoire and monthly archive imports

## Use the app

Open **Learn & practice**, choose your player library, and select **Build / resume repertoire**. The collection version is pinned when the build starts. Every game is queued; there is no 30-game limit. New imports need a build for their new collection version. Exercises appear while processing continues. Use the phase selector, choose an exercise, and play a move. Attempts are saved privately. Reveal the continuation to replay the searched alternatives. **Try again** returns to the original position.

**Pause** stops before the next game. The current search finishes within its 30-second budget plus engine cleanup. **Build / resume** continues pending games and retries failed games. A service restart returns interrupted repertoire tasks to the queue. Status shows completed, pending, failed, partial reviews, source months, and exercise counts. A complete scan means every queued game was attempted; a finite search cannot guarantee every missed opportunity was found.

When personal games finish, the optional archive fallback reads retained, published source frames, newest locally available month first, then older months. Both sides of every valid standard game are examined. It uses a durable shard / row-group / row / side cursor, not the 600-game teaching sample. If the newest official archive is not imported, the build waits for available sources and labels the month it actually uses. A newly imported month becomes eligible after the current source finishes; it does not reset the unfinished cursor. Archive record keys include the source checksum so revised monthly exports remain distinct. No archive is downloaded merely by enabling repertoire fallback.

Open **Pipelines → Fetch a monthly archive** after signing in. Choose **Latest available month**, a specific month, or paste an official monthly `.pgn.zst` / `.pgn.zst.torrent` link. Set a maximum compressed-download size in MiB and choose Python, parallel Python, or Spark. **Import selected month** queues one complete month. Torrent links resolve to the corresponding official HTTPS PGN archive; this app does not use BitTorrent peers.

The default ceiling is 100 MiB. A 31 GB archive needs a larger explicit ceiling, plus substantially more disk for decoded frames, parsed Parquet and PostgreSQL. The ceiling limits compressed download bytes, not total storage. Disk checks stop work before exhausting the configured reserve. The existing quality checks and exact source-record count still gate publication.

**Pause**, **Resume / retry**, and **Cancel** control archive jobs. The worker stops the active child process and retains downloaded partials, verified frames, transformed shards and staged database batches. Cancellation stops the job; it does not delete shared source files or an existing publication. A resume must use the same source, settings and pipeline code. After a code upgrade, an old pipeline run may need a fresh import rather than resume. The UI displays the failure; it never silently claims completion.

For automatic imports, choose the ceiling and pipeline, check **Automatically import each new latest month**, and save. The worker checks hourly and queues only the latest published month not already requested by that account. A failed, paused or cancelled month requires an explicit retry/new request. A source larger than the ceiling leaves an explanatory subscription error. Uncheck and save to stop future automatic jobs; pause/cancel an existing job separately. Automatic all-month backfills remain disabled.

## Game review

“Summarize this game” describes the players, result, opening, castling, substantial material changes, ending, selected position and available engine findings. Material facts are not a positional evaluation. “Why did I lose?” starts an owner-checked review of that exact game if none exists, shows factual context immediately, and refreshes the answer when the review completes. Engine findings describe detected score deteriorations, not a proven sole cause of defeat.

Position results stay visible while playing trial moves or replaying engine lines. Their original played position, trial line and profile remain labelled. The panel explicitly marks an earlier result when the current board differs. **Return to evaluated position** restores the search context. The recorded mainline is immutable.

## Models and execution

Migration `010_repertoire_archives.sql` adds builds, tasks, exercises, attempts, archive jobs and subscriptions. All account-facing lookups check ownership; writes require the existing session and CSRF token. Exercise solutions are withheld until an attempt/reveal. Attempts use an idempotency key bound to the exercise and move. Exercises keep the FEN, source game/record, ply, legal searched lines, score, engine budget and coverage. Phase is a heuristic: first 20 half-moves opening; later non-pawn material <=20 endgame; otherwise middlegame.

The engine worker runs one Stockfish thread (32 MiB hash) and prioritizes position / selected-game jobs before repertoire work. Mining uses up to 30 seconds per game and 10,000 base nodes. A candidate must lose >=150 centipawns relative to a searched alternative whose score is >=100 centipawns for the player. Accepted moves are searched alternatives within 30 centipawns of the best, plus any immediate legal checkmate. Other legal moves may also be good; this is a study repertoire, not a proof of unique puzzle solutions. Failed records are counted and retryable. Partial engine coverage is reported.

The separate `archive-worker` service uses the loader role and the runner image, with 2 CPUs and a 3 GiB memory ceiling. The web/study service cannot write warehouse tables. The loader receives only the operational study queue grants, not accounts, sessions, original private revisions or attempts. It spawns fixed Python module commands without a shell or Docker socket. Source links and redirects are restricted to the official HTTPS host. SHA256 values and record counts come from the official catalog; no checksum supplied by a browser is trusted. The archive pipeline verifies the whole compressed file, writes byte-bounded Parquet frames, transforms in batches, loads structured warehouse models and atomically publishes a month.

Start from the repository using existing `.env` credentials:

```bash
docker compose --profile study build study-api archive-worker
docker compose --profile web --profile study up -d --wait web study-api archive-worker
docker compose logs --tail 100 archive-worker
docker compose --profile study run --rm study-tests
docker compose --profile tools run --rm tests
```

Back up PostgreSQL **and** pipeline/study volumes. Jobs and repertoire cursors live in PostgreSQL; retained frame paths live in the pipeline volume. The existing backup/restore drill checks the database and artifacts. Container resources bound concurrent work; local validation does not establish 31 GB/month or multi-year production capacity.

Sources: [official monthly database](https://database.lichess.org/), [standard archive checksums](https://database.lichess.org/standard/sha256sums.txt). Catalog metadata was checked live on 2026-11-08; latest available was October 2026, 95,395,628 games, listed as 31 GB. Large current-month bulk ingestion remains a user-selected operation.
