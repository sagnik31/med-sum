"""
Measure the safety net: inject known faults into the answer keys, run the
validators and signals on each altered copy exactly as the graph's validate
and finish nodes do, and count how many faults reach review. No model.

    python -m evals.faultcheck [--out DIR] [--write-baseline]

A fault is the kind of mistake a model makes when it reads or parses a
page: a digit swapped, a decimal point moved, a unit or a range bound taken
for the value, a row dropped, a medicine invented, a remark put on the
wrong test, a day and month swapped. Each one is applied to one item of
one document's parse at a time, so every result names its document and
item. Two faults are not the parse's: `transcription_misread` changes a
value in the transcription as well, so every line agrees with it and only
the PDF's text layer can tell; `wrong_patient` uploads the document to
another patient of the dataset.

A fault is **flagged** when the altered copy raises a routing signal that
the clean document does not raise; **routed** when it ends needs_review
(a document built to go to review, such as d15, is routed whatever the
fault); **on target** when a new hit is on the item the fault changed, so
the reviewer is sent to the right line. Faults that are never flagged are
the blind spots, listed by name.

The clean documents are checked too: each must end in the manifest's
`expected_end_state` with exactly its `expected_signals`.

The gate (exit 1, and the test of the same name) fails when a clean
document ends anywhere else, when a fault's flagged rate drops below the
committed baseline (`scripts/evals/faultcheck_baseline.json`), or when a
fault has no baseline. `--write-baseline` writes the current counts as the
new baseline; commit it with the change that moved them.
"""

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dataset.build import DATASET_DIR
from derive import build_record
from evals.keys import (
    answer_key,
    documents,
    expected_patient,
    manifest,
    parse_of,
    patient_of,
    with_text_layers,
)
from evals.stats import wilson
from pipeline import signals
from record.document import PageTranscription
from record.printed import (
    DocumentType,
    LabReportParse,
    PrescriptionParse,
    PrintedLabResult,
)
from validators import ExpectedPatient, Finding, validate

RESULTS_DIR = DATASET_DIR.parent / "results"
BASELINE = DATASET_DIR.parent / "faultcheck_baseline.json"

# Validator 7 compares dates with today; fixed so results don't drift.
TODAY = date(2026, 11, 9)

Parse = LabReportParse | PrescriptionParse


@dataclass(frozen=True)
class Case:
    """One document as the validate node has it, and where it goes."""

    doc_id: str
    doc_type: DocumentType
    pages: list[PageTranscription]
    parse: Parse | None
    expected_patient: ExpectedPatient


@dataclass(frozen=True)
class Mutant:
    fault: str
    doc_id: str
    # The item the fault changed (obs-3, med-1, rem-2), "document" for a
    # header or patient fault, None for one with nothing left to point at
    # (a dropped row).
    target: str | None
    case: Case


@dataclass(frozen=True)
class Outcome:
    findings: list[Finding]
    hits: list[signals.SignalHit]

    @property
    def end_state(self) -> str:
        return "needs_review" if signals.needs_review(self.hits) else "verified"

    def routing(self) -> set[tuple]:
        return {(h.signal, h.item_id, h.field) for h in self.hits if h.routes}

    def routing_ids(self) -> list[str]:
        return sorted({h.signal for h in self.hits if h.routes})


@dataclass(frozen=True)
class Result:
    fault: str
    doc_id: str
    target: str | None
    flagged: bool
    routed: bool
    on_target: bool
    signals: list[str]  # the new routing signals
    validators: list[str]  # the validators behind them


def clean_case(doc_id: str, dataset_dir: Path = DATASET_DIR) -> Case:
    key = answer_key(doc_id, dataset_dir)
    return Case(
        doc_id,
        key.document.type,
        with_text_layers(doc_id, key.pages, dataset_dir),
        parse_of(key),
        expected_patient(doc_id, dataset_dir),
    )


def outcome(case: Case) -> Outcome:
    """What validate and finish make of a case: findings and signals."""
    record = build_record(
        document_id=case.doc_id,
        doc_type=case.doc_type,
        pages=case.pages,
        has_text_layer=any(p.text_layer for p in case.pages),
        parse=case.parse,
    )
    findings = (
        validate(record, expected_patient=case.expected_patient, today=TODAY)
        if case.parse is not None
        else []
    )
    hits = signals.collect(
        doc_type=case.doc_type,
        record=record,
        parsed=case.parse is not None,
        findings=findings,
    )
    return Outcome(findings, hits)


# -------------------------------------------------------------------
# The faults
# -------------------------------------------------------------------
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_PLAIN = re.compile(r"\d+(?:\.\d+)?")
_DMY = re.compile(r"(\d{2})([/-])(\d{2})\2(\d{4})")
_FREQUENCIES = {"OD": "BD", "BD": "TDS", "TDS": "BD", "HS": "OD", "SOS": "OD"}


def digit_swap(value: str) -> str | None:
    """The first two adjacent digits that differ, swapped: 14.8 -> 41.8."""
    for i in range(len(value) - 1):
        a, b = value[i], value[i + 1]
        if a.isdigit() and b.isdigit() and a != b:
            return value[:i] + b + a + value[i + 2 :]
    return None


def decimal_shift(value: str) -> str | None:
    """The decimal point one place right: 8.9 -> 89, 1.25 -> 12.5, 120 -> 1200."""
    if not _PLAIN.fullmatch(value):
        return None
    if "." not in value:
        return value + "0"
    i = value.index(".")
    digits = value.replace(".", "")
    shifted = digits[: i + 1] + ("." + digits[i + 1 :] if i + 1 < len(digits) else "")
    shifted = shifted.lstrip("0")
    return "0" + shifted if shifted.startswith(".") or not shifted else shifted


def misread_digit(value: str) -> str | None:
    """The last digit read as the next one: 8.9 -> 8.0, 142 -> 143."""
    for i in range(len(value) - 1, -1, -1):
        if value[i].isdigit():
            return value[:i] + str((int(value[i]) + 1) % 10) + value[i + 1 :]
    return None


def _copy(case: Case, **changes) -> Case:
    parse = changes.pop("parse", None) or case.parse.model_copy(deep=True)
    return Case(
        case.doc_id,
        case.doc_type,
        changes.pop("pages", case.pages),
        parse,
        changes.pop("expected_patient", case.expected_patient),
    )


def _result_faults(case: Case) -> Iterator[Mutant]:
    results = case.parse.results
    units = sorted({r.unit for r in results if r.unit})
    names = [r.test_name for r in results]
    for i, r in enumerate(results):
        target = f"obs-{i + 1}"

        def changed(fault: str, **values) -> Mutant:
            mutant = _copy(case)
            item = mutant.parse.results[i]
            for name, value in values.items():
                setattr(item, name, value)
            return Mutant(fault, case.doc_id, target, mutant)

        if (v := digit_swap(r.value_text)) is not None and _PLAIN.fullmatch(
            r.value_text.replace(",", "")
        ):
            yield changed("digit_swap", value_text=v)
        if (v := decimal_shift(r.value_text)) is not None:
            yield changed("decimal_shift", value_text=v)
        if r.unit:
            other = next((u for u in units if u != r.unit), None)
            other = other or ("mmol/L" if r.unit != "mmol/L" else "mg/dL")
            yield changed("unit_swap", unit=other)
        if r.reference_ranges and _PLAIN.fullmatch(r.value_text.replace(",", "")):
            bounds = _NUMBER.findall(r.reference_ranges[0])
            bound = next((b for b in bounds if b != r.value_text), None)
            if bound is not None:
                yield changed("range_bound", value_text=bound)
        if misread := _misread(case, i):
            yield misread

        mutant = _copy(case)
        del mutant.parse.results[i]
        yield Mutant("dropped_result", case.doc_id, None, mutant)

    if results:
        mutant = _copy(case)
        last = mutant.parse.results[-1]
        mutant.parse.results.append(
            PrintedLabResult(
                citation=last.citation,
                panel=last.panel,
                test_name="Serum Potassium",
                value_text="4.2",
                unit="mmol/L",
                reference_ranges=["3.5 - 5.1"],
                flag_printed=None,
            )
        )
        yield Mutant("invented_result", case.doc_id, f"obs-{len(results) + 1}", mutant)

    for i, remark in enumerate(case.parse.remarks):
        if remark.scope.value != "test" or remark.test_name is None:
            continue
        other = next((n for n in names if n != remark.test_name), None)
        if other is None:
            continue
        mutant = _copy(case)
        mutant.parse.remarks[i].test_name = other
        yield Mutant("remark_moved", case.doc_id, f"rem-{i + 1}", mutant)


def _misread(case: Case, i: int) -> Mutant | None:
    """
    A value misread in the transcription itself: the page line, the
    citation's quote and the parse all carry the wrong digit, so the
    record agrees with its page and only the text layer can disagree.
    """
    r = case.parse.results[i]
    if not _PLAIN.fullmatch(r.value_text):
        return None
    new = misread_digit(r.value_text)
    c = r.citation
    if c.first_line != c.last_line:
        return None
    page = case.pages[c.page - 1]
    line = page.lines[c.first_line - 1]
    cell = f"| {r.value_text} |"
    if line != c.quote or line.count(cell) != 1:
        return None
    new_line = line.replace(cell, f"| {new} |")
    lines = list(page.lines)
    lines[c.first_line - 1] = new_line
    pages = list(case.pages)
    pages[c.page - 1] = page.model_copy(update={"lines": lines})
    mutant = _copy(case, pages=pages)
    item = mutant.parse.results[i]
    item.value_text = new
    item.citation = item.citation.model_copy(update={"quote": new_line})
    return Mutant("transcription_misread", case.doc_id, f"obs-{i + 1}", mutant)


def _medicine_faults(case: Case) -> Iterator[Mutant]:
    medicines = case.parse.medicines
    for i, m in enumerate(medicines):
        target = f"med-{i + 1}"

        def changed(fault: str, **values) -> Mutant:
            mutant = _copy(case)
            item = mutant.parse.medicines[i]
            for name, value in values.items():
                setattr(item, name, value)
            return Mutant(fault, case.doc_id, target, mutant)

        if m.strength and (n := _NUMBER.search(m.strength)):
            shifted = decimal_shift(n.group())
            if shifted is not None:
                strength = m.strength[: n.start()] + shifted + m.strength[n.end() :]
                yield changed("strength_shift", strength=strength)
        if m.frequency:
            frequency = _FREQUENCIES.get(m.frequency.upper())
            if frequency is None and re.fullmatch(r"[01½](-[01½])+", m.frequency):
                j = m.frequency.index("0") if "0" in m.frequency else 0
                swap = "1" if m.frequency[j] == "0" else "0"
                frequency = m.frequency[:j] + swap + m.frequency[j + 1 :]
            if frequency is not None:
                yield changed("frequency_change", frequency=frequency)

        mutant = _copy(case)
        del mutant.parse.medicines[i]
        yield Mutant("dropped_medicine", case.doc_id, None, mutant)

    if medicines:
        mutant = _copy(case)
        last = mutant.parse.medicines[-1]
        mutant.parse.medicines.append(
            last.model_copy(update={"name": "Metoprolol", "strength": "50 mg"})
        )
        yield Mutant(
            "invented_medicine", case.doc_id, f"med-{len(medicines) + 1}", mutant
        )


def _document_faults(
    case: Case, clean: Outcome, patients: list[str]
) -> Iterator[Mutant]:
    details = case.parse.details
    for name in ("report_date", "collection_date", "prescription_date"):
        printed = getattr(details, name)
        if printed is None:
            continue
        m = _DMY.match(printed.text)
        if not m or m[1] == m[3] or int(m[1]) > 12 or int(m[3]) > 12:
            continue
        swapped = f"{m[3]}{m[2]}{m[1]}{m[2]}{m[4]}" + printed.text[m.end() :]
        mutant = _copy(case)
        getattr(mutant.parse.details, name).text = swapped
        yield Mutant("date_swap", case.doc_id, "document", mutant)

    if "LC-13" not in clean.routing_ids():
        uploaded_to = documents()[case.doc_id]["patient"]
        other = next(p for p in patients if p != uploaded_to)
        yield Mutant(
            "wrong_patient",
            case.doc_id,
            "document",
            _copy(case, expected_patient=patient_of(other)),
        )


def mutants(case: Case, clean: Outcome) -> Iterator[Mutant]:
    """Every fault this document can carry, one at a time."""
    if case.parse is None:
        return
    patients = [p["key"] for p in manifest()["patients"]]
    if isinstance(case.parse, LabReportParse):
        yield from _result_faults(case)
    else:
        yield from _medicine_faults(case)
    yield from _document_faults(case, clean, patients)


# -------------------------------------------------------------------
# Running them
# -------------------------------------------------------------------
def judge(mutant: Mutant, clean: Outcome) -> Result:
    after = outcome(mutant.case)
    before = clean.routing()
    new = [
        h
        for h in after.hits
        if h.routes and (h.signal, h.item_id, h.field) not in before
    ]
    validators = sorted(
        {
            after.findings[h.finding].validator.value
            for h in new
            if h.finding is not None
        }
    )
    if mutant.target == "document":
        on_target = any(h.item_id in (None, "patient") for h in new)
    else:
        on_target = any(h.item_id == mutant.target for h in new)
    return Result(
        fault=mutant.fault,
        doc_id=mutant.doc_id,
        target=mutant.target,
        flagged=bool(new),
        routed=after.end_state == "needs_review",
        on_target=on_target and mutant.target is not None,
        signals=sorted({h.signal for h in new}),
        validators=validators,
    )


@dataclass
class CleanResult:
    doc_id: str
    expected_end_state: str
    end_state: str
    expected_signals: list[str]
    signals: list[str]

    @property
    def right(self) -> bool:
        return (self.end_state, self.signals) == (
            self.expected_end_state,
            sorted(self.expected_signals),
        )


@dataclass
class FaultCheck:
    clean: list[CleanResult] = field(default_factory=list)
    results: list[Result] = field(default_factory=list)


def run(dataset_dir: Path = DATASET_DIR) -> FaultCheck:
    check = FaultCheck()
    for doc_id, entry in documents(dataset_dir).items():
        case = clean_case(doc_id, dataset_dir)
        clean = outcome(case)
        check.clean.append(
            CleanResult(
                doc_id,
                entry["expected_end_state"],
                clean.end_state,
                entry["expected_signals"],
                clean.routing_ids(),
            )
        )
        for mutant in mutants(case, clean):
            check.results.append(judge(mutant, clean))
    return check


# -------------------------------------------------------------------
# Summary, gate, report
# -------------------------------------------------------------------
def by_fault(check: FaultCheck) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for fault in sorted({r.fault for r in check.results}):
        rows = [r for r in check.results if r.fault == fault]
        validators = Counter(v for r in rows for v in r.validators)
        sigs = Counter(s for r in rows for s in r.signals)
        out[fault] = {
            "injected": len(rows),
            "flagged": wilson(sum(r.flagged for r in rows), len(rows)).as_dict(),
            "routed": sum(r.routed for r in rows),
            "on_target": sum(r.on_target for r in rows),
            "validators": dict(sorted(validators.items())),
            "signals": dict(sorted(sigs.items())),
            "missed": [
                {"document": r.doc_id, "target": r.target}
                for r in rows
                if not r.flagged
            ],
        }
    return out


def totals(check: FaultCheck) -> dict:
    rows = check.results
    split = {d: e.get("split", "test") for d, e in documents().items()}
    return {
        "injected": len(rows),
        "flagged": wilson(sum(r.flagged for r in rows), len(rows)).as_dict(),
        "routed": sum(r.routed for r in rows),
        "on_target": sum(r.on_target for r in rows),
        # Whether the checks catch as much on layouts they weren't built on.
        "by_split": {
            name: wilson(
                sum(r.flagged for r in rows if split[r.doc_id] == name),
                sum(1 for r in rows if split[r.doc_id] == name),
            ).as_dict()
            for name in sorted(set(split.values()))
        },
    }


def baseline_of(check: FaultCheck) -> dict[str, dict]:
    return {
        fault: {"injected": s["injected"], "flagged": s["flagged"]["hits"]}
        for fault, s in by_fault(check).items()
    }


def gate(check: FaultCheck, baseline: dict[str, dict]) -> list[str]:
    """Why the check fails, or nothing when it passes."""
    failures = [
        f"{c.doc_id} ends {c.end_state} {c.signals}, expected "
        f"{c.expected_end_state} {sorted(c.expected_signals)}"
        for c in check.clean
        if not c.right
    ]
    for fault, s in by_fault(check).items():
        base = baseline.get(fault)
        if base is None:
            failures.append(f"{fault}: no baseline (run with --write-baseline)")
            continue
        flagged, injected = s["flagged"]["hits"], s["injected"]
        if flagged * base["injected"] < base["flagged"] * injected:
            failures.append(
                f"{fault}: flagged {flagged}/{injected}, baseline "
                f"{base['flagged']}/{base['injected']}"
            )
    return failures


def build_report(check: FaultCheck, baseline: dict[str, dict]) -> dict:
    return {
        "today": TODAY.isoformat(),
        "clean": [
            {
                "document": c.doc_id,
                "expected_end_state": c.expected_end_state,
                "end_state": c.end_state,
                "expected_signals": sorted(c.expected_signals),
                "signals": c.signals,
                "right": c.right,
            }
            for c in check.clean
        ],
        "totals": totals(check),
        "faults": by_fault(check),
        "gate": gate(check, baseline),
    }


def _ci(d: dict) -> str:
    return wilson(d["hits"], d["total"]).text()


def render_markdown(report: dict) -> str:
    right = sum(c["right"] for c in report["clean"])
    t = report["totals"]
    lines = [
        "# Fault check",
        "",
        f"Validators and signals on the answer keys, with one fault injected at "
        f"a time (validator 7's today: {report['today']}).",
        "",
        f"- **Clean documents ending as expected:** {right}/{len(report['clean'])}",
        f"- **Faults flagged (a new routing signal):** {_ci(t['flagged'])}",
        f"- **Flagged on the item the fault changed:** {t['on_target']}/{t['injected']}",
        "- **Flagged, by split:** "
        + "; ".join(f"{name} {_ci(c)}" for name, c in t["by_split"].items()),
        f"- **Gate:** {'pass' if not report['gate'] else 'FAIL'}",
        *(f"  - {f}" for f in report["gate"]),
        "",
        "| Fault | Injected | Flagged | On target | Routed | Caught by |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for fault, s in report["faults"].items():
        caught = ", ".join(f"{v} {n}" for v, n in s["signals"].items()) or "-"
        lines.append(
            f"| {fault} | {s['injected']} | {_ci(s['flagged'])} | "
            f"{s['on_target']} | {s['routed']} | {caught} |"
        )
    blind = {f: s["missed"] for f, s in report["faults"].items() if s["missed"]}
    lines += ["", "## Not flagged", ""]
    if not blind:
        lines.append("Every injected fault was flagged.")
    for fault, missed in blind.items():
        per_doc: dict[str, list[str]] = {}
        for m in missed:
            per_doc.setdefault(m["document"], []).append(m["target"] or "")
        where = "; ".join(
            f"{doc} {' '.join(t for t in targets if t)}".strip()
            if any(targets)
            else f"{doc} x{len(targets)}"
            for doc, targets in per_doc.items()
        )
        lines.append(f"- **{fault}** ({len(missed)}): {where}")
    wrong = [c for c in report["clean"] if not c["right"]]
    if wrong:
        lines += ["", "## Clean documents ending elsewhere", ""]
        lines += [
            f"- {c['document']}: {c['end_state']} {c['signals']}, expected "
            f"{c['expected_end_state']} {c['expected_signals']}"
            for c in wrong
        ]
    return "\n".join(lines) + "\n"


def load_baseline(path: Path = BASELINE) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.faultcheck")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "faultcheck")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)

    check = run()
    if args.write_baseline:
        BASELINE.write_text(
            json.dumps(baseline_of(check), indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {BASELINE}")
    report = build_report(check, load_baseline())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    t = report["totals"]
    print(f"faults flagged: {_ci(t['flagged'])}; wrote {args.out}")
    for failure in report["gate"]:
        print(f"FAIL {failure}")
    return 1 if report["gate"] else 0


if __name__ == "__main__":
    sys.exit(main())
