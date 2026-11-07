# Case study: turning game history into a practice session

## Outline

1. Start with a player's question.
2. Preserve trustworthy game history.
3. Connect a finding to a position and a practice action.
4. Keep the result reproducible and private.

## The practical problem

A long PGN export is difficult to study directly. An overall win percentage does
not explain a mistake. A useful workflow narrows the selection, links a finding
to a real game, and ends with something concrete to practise.

## The working approach

Create a local app account, upload a PGN or provide a public Lichess games API
link, and open the resulting player library. Dates are analysis filters rather
than import restrictions. Background imports process chunks, reuse unchanged
game payloads and publish a new collection atomically. Saved studies retain the
exact older collection and filters.

The study page offers at most three focus cards, five common openings and ten
games per page. Follow a linked loss into its board before interpreting an opening
score. Explicit Stockfish reviews add search-limited tactical evidence and legal
continuations. Practice notes record what to check next time.

## What was verified

The authorised real-history acceptance loaded 12,519 standard games, reused the
existing October collection and explained six empty or unsupported records.
Storage checks found one payload per unchanged game, preserved earlier versions,
and complete recurring-position indexing. Two-account tests exercise private
resource boundaries, and desktop/mobile checks cover the complete study loop.
The executed record is in [workspace validation](personal-workspace-validation.md).

## What the result supports

This is a local game-review and research workflow. Opening scores describe
observed results; they do not identify a causal weakness. Tactical advice requires
an explicit engine review with stated budgets. Computed questions can summarise
supported selections; they are not an unrestricted AI coach or a promise of rating
improvement. Public hosting and broader authentication operations require separate
deployment work.

See the [Personal Study guide](personal-study.md) for imports, data models, account
access, recovery and reproducible exports.
