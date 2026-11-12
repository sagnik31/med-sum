"""
Compare two eval runs and gate the second on the first.

    python -m evals.compare A B

A and B are history entries (scripts/evals/history/*.json) or report.json
files. For each model in A, B must have it too, and each metric may move
the wrong way by at most its tolerance. Unsafe errors may never rise,
whether over all clinical items or inside the auto-verified documents.
Prints a table of every metric and exits 1 when any check fails.

Tolerances are defaults, chosen for a 17-document dataset where one item
moves field F1 by about half a point; tighten them as the dataset grows.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

from evals.history import read

# Metric -> how far it may fall (a rate, 0.01 = one point). Higher is better.
MAY_FALL = {
    "field_f1": 0.01,
    "derived_f1": 0.01,
    "medicine_recall": 0.0,
    "value_recall": 0.01,
    "end_state_right": 0.0,
}
# Counts that may never rise.
NEVER_RISE = ("unsafe", "unsafe_in_verified")


@dataclass(frozen=True)
class Check:
    model: str
    metric: str
    before: float | int | None
    after: float | int | None
    ok: bool
    rule: str

    @property
    def change(self) -> str:
        if self.before is None or self.after is None:
            return "-"
        d = self.after - self.before
        return f"{d:+.1%}" if isinstance(d, float) else f"{d:+d}"


def _rate(c: dict | None) -> float | None:
    return c["hits"] / c["total"] if c and c["total"] else None


def _values(m: dict) -> dict:
    r = m.get("routing") or {}
    return {
        "field_f1": m["field_f1"],
        "derived_f1": m["derived_f1"],
        "medicine_recall": m["medicine_recall"],
        "value_recall": m["value_recall"],
        "end_state_right": _rate(r.get("end_state_right")),
        "unsafe": m["unsafe"],
        "unsafe_in_verified": (r.get("unsafe_in_verified") or {}).get("hits"),
    }


def compare(a: dict, b: dict) -> list[Check]:
    checks = []
    for model, before_m in a["models"].items():
        after_m = b["models"].get(model)
        if after_m is None:
            checks.append(Check(model, "model", None, None, False, "missing in B"))
            continue
        before, after = _values(before_m), _values(after_m)
        for metric, fall in MAY_FALL.items():
            x, y = before[metric], after[metric]
            ok = x is None or (y is not None and y >= x - fall - 1e-12)
            rule = f"may fall {fall:.0%}" if fall else "may not fall"
            checks.append(Check(model, metric, x, y, ok, rule))
        for metric in NEVER_RISE:
            x, y = before[metric], after[metric]
            ok = x is None or (y is not None and y <= x)
            checks.append(Check(model, metric, x, y, ok, "may not rise"))
    return checks


def _show(v) -> str:
    if v is None:
        return "-"
    return f"{v:.1%}" if isinstance(v, float) else str(v)


def render(a: dict, b: dict, checks: list[Check]) -> str:
    lines = [
        f"A: {a['label']} ({a['generated_at']})",
        f"B: {b['label']} ({b['generated_at']})",
        "",
        "| Model | Metric | A | B | Change | Rule | |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in checks:
        lines.append(
            f"| `{c.model}` | {c.metric} | {_show(c.before)} | {_show(c.after)} | "
            f"{c.change} | {c.rule} | {'ok' if c.ok else '**FAIL**'} |"
        )
    failed = [c for c in checks if not c.ok]
    lines += ["", "PASS" if not failed else f"FAIL: {len(failed)} check(s)"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m evals.compare A B", file=sys.stderr)
        return 2
    a, b = read(Path(argv[0])), read(Path(argv[1]))
    checks = compare(a, b)
    sys.stdout.write(render(a, b, checks))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
