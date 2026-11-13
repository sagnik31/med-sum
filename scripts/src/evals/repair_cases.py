"""
The repair eval's cases: parses that need a repair, made by injecting one
fault into an answer key's parse (evals/faultcheck.py), so both repair
variants repair exactly the same parses.

    python -m evals.repair_cases [--write]

A clean dataset document rarely needs a repair, so a run over the dataset
alone would compare the two repairs on one or two cases. Every fault the
fault check injects that leaves an *error* finding the clean document
doesn't have (only errors send a parse to repair) is a case: a changed
value, a swapped unit, a dropped or invented row or medicine, a moved
remark, a swapped date. A fault that changes the page itself (a
transcription misread) or the patient the document was uploaded for is
not one: no re-parse can fix those.

Each case is named <doc>-<fault>-<n> (the n-th such fault in that
document) and carries a fingerprint of its faulted parse, so the
committed list (scripts/evals/repair_cases.json) fails its test when the
dataset or the faults change. Two subsets keep a live run short: "core",
one case of each fault from the test split, and "extended", core plus one
of each fault from the held-out split, each spread over as many documents
as the faults allow. Case parses are synthetic.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from dataset.build import DATASET_DIR
from evals import faultcheck
from evals.faultcheck import Case
from evals.keys import documents
from validators import Severity

CASES_FILE = DATASET_DIR.parent / "repair_cases.json"
VERSION = 1
# Faults that change what the parse can't: the page, or the patient.
NOT_REPAIRABLE = ("transcription_misread", "wrong_patient")
SUBSETS = ("core", "extended", "all")


@dataclass(frozen=True)
class RepairCase:
    id: str
    doc_id: str
    split: str
    fault: str
    target: str | None
    errors: list[str]  # the validators behind the new error findings
    fingerprint: str  # of the faulted parse
    subset: str | None = None  # core, extended, or None (all only)


def _errors(findings) -> set[tuple]:
    return {
        (f.validator.value, f.item_id, f.field)
        for f in findings
        if f.severity is Severity.ERROR
    }


def _fingerprint(case: Case) -> str:
    data = case.parse.model_dump_json().encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()[:16]


def _walk(dataset_dir: Path) -> Iterator[tuple[RepairCase, Case]]:
    for doc_id, entry in documents(dataset_dir).items():
        clean_case = faultcheck.clean_case(doc_id, dataset_dir)
        clean = faultcheck.outcome(clean_case)
        before = _errors(clean.findings)
        seen: dict[str, int] = {}
        for mutant in faultcheck.mutants(clean_case, clean):
            if mutant.fault in NOT_REPAIRABLE:
                continue
            new = _errors(faultcheck.outcome(mutant.case).findings) - before
            if not new:
                continue
            seen[mutant.fault] = seen.get(mutant.fault, 0) + 1
            yield (
                RepairCase(
                    id=f"{doc_id}-{mutant.fault}-{seen[mutant.fault]}",
                    doc_id=doc_id,
                    split=entry["split"],
                    fault=mutant.fault,
                    target=mutant.target,
                    errors=sorted({v for v, _, _ in new}),
                    fingerprint=_fingerprint(mutant.case),
                ),
                mutant.case,
            )


def _spread(cases: list[RepairCase], split: str) -> set[str]:
    """
    One case per fault from a split, spread over its documents: each fault
    (in name order) takes its first case in a document no earlier fault
    took, or its first case when every document it has is taken.
    """
    chosen, used = set(), set()
    for fault in sorted({c.fault for c in cases if c.split == split}):
        mine = [c for c in cases if c.split == split and c.fault == fault]
        pick = next((c for c in mine if c.doc_id not in used), mine[0])
        chosen.add(pick.id)
        used.add(pick.doc_id)
    return chosen


def _with_subsets(cases: list[RepairCase]) -> list[RepairCase]:
    core, extended = _spread(cases, "test"), _spread(cases, "held_out")
    out = []
    for c in cases:
        subset = "core" if c.id in core else "extended" if c.id in extended else None
        out.append(RepairCase(**{**asdict(c), "subset": subset}))
    return out


def build(dataset_dir: Path = DATASET_DIR) -> list[RepairCase]:
    """Every repair case, in document order, with its subset."""
    return _with_subsets([c for c, _ in _walk(dataset_dir)])


def case_parse(case_id: str, dataset_dir: Path = DATASET_DIR) -> Case:
    """The faulted document of one case, rebuilt; checked against its fingerprint."""
    wanted = load(dataset_dir)[case_id]
    for c, mutant in _walk(dataset_dir):
        if c.id == case_id:
            if c.fingerprint != wanted.fingerprint:
                raise ValueError(
                    f"{case_id}: the faulted parse changed; rebuild the cases"
                )
            return mutant
    raise KeyError(f"{case_id}: no such case any more; rebuild the cases")


def in_subset(cases: dict[str, RepairCase], subset: str) -> list[RepairCase]:
    if subset not in SUBSETS:
        raise ValueError(f"subset must be one of {', '.join(SUBSETS)}")
    if subset == "all":
        return list(cases.values())
    keep = ("core",) if subset == "core" else ("core", "extended")
    return [c for c in cases.values() if c.subset in keep]


def as_json(cases: list[RepairCase]) -> str:
    data = {
        "version": VERSION,
        "about": "Repair eval cases: one injected fault each (python -m evals.repair_cases)",
        "cases": [asdict(c) for c in cases],
    }
    return json.dumps(data, indent=1) + "\n"


def load(dataset_dir: Path = DATASET_DIR) -> dict[str, RepairCase]:
    path = dataset_dir.parent / CASES_FILE.name
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != VERSION:
        raise ValueError(f"{path}: version {data.get('version')!r}")
    return {c["id"]: RepairCase(**c) for c in data["cases"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.repair_cases")
    parser.add_argument("--write", action="store_true", help=f"write {CASES_FILE.name}")
    args = parser.parse_args(argv)
    cases = build()
    by = {}
    for c in cases:
        by.setdefault(c.fault, [0, 0])
        by[c.fault][0] += 1
        by[c.fault][1] += c.subset is not None
    for fault, (n, chosen) in sorted(by.items()):
        print(f"{fault:20} {n:4} cases, {chosen} in core/extended")
    print(
        f"{len(cases)} cases; core {sum(c.subset == 'core' for c in cases)}, "
        f"extended {sum(c.subset is not None for c in cases)}"
    )
    if args.write:
        CASES_FILE.write_text(as_json(cases), encoding="utf-8")
        print(f"wrote {CASES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
