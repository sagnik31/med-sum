"""
Re-reading a page whose transcription hit the output cap: once, as two
overlapping halves, joined in code.

A page that runs past MAX_TOKENS_PER_PAGE comes back cut off, and every
model hits the same cap on it. Each half holds a little over half the
page's text, so each fits. The halves overlap by 15% of the page height,
so the lines in that band are read twice; the halves are joined where
their readings agree.

The join is strict, because a wrong join would duplicate or lose lines
that citations point at. The bottom half must start with lines the top
half ends with: the longest run of at least MIN_OVERLAP identical
non-blank lines (spacing at the ends ignored), found at one place only.
Each half may also have a few lines cut through by its edge, read
differently or not at all, so the run may sit up to EDGE_LINES non-blank
lines from the top half's end and the bottom half's start. No such run,
or more than one, and the page is not joined: it keeps the capped
reading's signal (LC-02) for a clinician. Repair still never re-reads a
page: this is only for a reading that is known to be incomplete.
See docs/adr/0002-two-model-steps.md.
"""

# Fractions of the page height each half covers, top and bottom.
HALVES = ((0.0, 0.575), (0.425, 1.0))
MIN_OVERLAP = 2
EDGE_LINES = 3


def _key(line: str) -> str:
    return line.strip()


def join_halves(top: list[str], bottom: list[str]) -> tuple[list[str], int] | None:
    """
    The page's lines from its two halves, and how many lines they shared;
    None when there is no single exact overlap to join them on.
    """
    t = [(i, _key(x)) for i, x in enumerate(top) if _key(x)]
    b = [(i, _key(x)) for i, x in enumerate(bottom) if _key(x)]
    best, at = 0, []
    for ti in range(len(t)):
        for bi in range(min(len(b), EDGE_LINES + 1)):
            k = 0
            while ti + k < len(t) and bi + k < len(b) and t[ti + k][1] == b[bi + k][1]:
                k += 1
            # The run must reach the top half's last lines (but for its edge).
            if k < MIN_OVERLAP or len(t) - (ti + k) > EDGE_LINES:
                continue
            if k > best:
                best, at = k, [(ti, bi)]
            elif k == best:
                at.append((ti, bi))
    if len(at) != 1:
        return None
    ti, bi = at[0]
    # Top's lines before the shared run, then bottom's from the run on.
    return top[: t[ti][0]] + bottom[b[bi][0] :], best
