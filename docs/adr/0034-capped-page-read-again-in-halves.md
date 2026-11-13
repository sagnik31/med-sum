# 0034. A page cut off by the output cap is read again in two halves

- **Status:** Accepted, 2026-11-13. Built.

## Context

Each page's transcription call has an output cap of 4,096 tokens, so a model stuck in a loop can't run forever. A real page that needs more than that comes back cut off on both models. The page then had no lines at all: nothing on it could be cited, and the document went to review with LC-02. Repair never re-reads a page (see [0015](0015-repair-re-parses-only.md)), because at temperature 0 the same model reads the same image the same way. But a capped page is different: the reading is known to be incomplete, and a smaller image needs fewer tokens.

## Decision

- **Once, after a cap on every model,** the page image is cut into two overlapping bands, the top 57.5% and the bottom 57.5%, so the middle 15% is read twice. Each band is transcribed with the same page prompt and cap.
- **The halves are joined in code, strictly.** The bottom half must begin with a run of at least two non-blank lines that the top half ends with, compared exactly (spacing at the ends ignored). Up to three lines at each half's cut edge may differ or be missing, since an edge can cut through a line. The longest such run is used, and it must be found in one place only.
- **Anything else keeps LC-02**: a half capped again, no shared run, or a run found in two places. The page keeps no lines, as before. A re-read never replaces a complete reading.
- **Every call is recorded on the page's step**, the capped readings and both halves, and the step's output says how the re-read went (`joined` with the overlap's length, or `not joined` with the reason).

## Alternatives rejected

- **Raise the cap.** It is there to stop runaway output, and a looping model would run longer before being stopped.
- **Re-read the page whole, or with another prompt.** At temperature 0 the same model gives the same reading, and it would hit the same cap.
- **Join on a fuzzy match.** A wrong join duplicates or drops lines that citations point at. A clinician reviewing a capped page is safer than a silent misjoin.
- **Re-read lines marked `[unreadable]` (LC-01) the same way.** Transcriptions have no coordinates, so a crop can't be aimed at one line.

## Consequences

- A page with a little more text than one call holds now reads completely, at the cost of two more calls of a few minutes each.
- A page laid out in two columns can't be joined: each half reads one column's part and then the other's, so the halves share no run of lines. Such a page keeps LC-02.
- The model joins lines that the print wraps into one long line, and a long joined line rarely comes out the same in both halves. Pages of wrapped paragraphs may not join; pages of separate lines (tables, lists, entries) do.
- The dataset's d25 ends with a page of fine print built to run past the cap.

## What would change it

Live evals showing joins that lose or duplicate lines, or capped pages that the halves fail to join often enough to try another split (three bands, or columns).
