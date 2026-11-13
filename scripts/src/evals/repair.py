"""
The repair eval: the fixed re-parse and the tool repair on the same
faulted parses (evals/repair_cases.py), for each model.

    python -m evals.repair --live   [--model M ...] [--variant fixed|tools|both]
                                    [--subset core|extended|all] [--cases id,id]
    python -m evals.repair --replay [same options]

Each case starts where the graph would be after a parse with that fault:
the answer key's pages (with their text layers), the faulted parse in
parts, and validate's error findings. Then the graph's own repair and
validate nodes run under its own routing, with the model as the primary,
until the validators find no error or the model's attempts are used (the
first parse plus two repairs). The fallback is not run: the comparison
is of one model's repairs. MEDSUM_REPAIR is set per variant.

Per model and variant the report gives: cases repaired (no error left,
with a Wilson interval), repairs per case, model calls, tokens and seconds
per repair, unsafe errors after repair (invented or changed clinical
values, scored against the clean answer key), and for the tool repair the
tool calls: by tool, malformed, unknown, not found, numbers sent as text,
over the per-turn limit, and whether the fix came in a reading turn.
`verdict` says which variant the numbers support per model: the tool
repair only if it repairs at least as many cases, leaves no more unsafe
errors and is not slower per repair. It never changes the default.

Like evals.run, a live run records cassettes (cassettes/repair/<variant>/
<model>/<case>.json) and is kept: its report goes to the committed
reports/<name>/; a replay, or --no-history, goes to the gitignored
results/. A live run of the core subset on both models and both variants
is about 44 repairs of a few minutes each on a CPU: ask before one.
"""

import argparse
import json
import os
import statistics
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import llm
import model_health
from dataset.build import DATASET_DIR
from evals import history, repair_cases
from evals.cassettes import Cassette, CassetteMiss, RecordingClient, ReplayClient
from evals.faultcheck import TODAY
from evals.keys import answer_key, split_parse
from evals.run import RESULTS_DIR, EvalDatabase, insert_document
from evals.score import score_document
from evals.stats import wilson
from observability import start_run
from pipeline import nodes, parts, routing
from pipeline.graph import GRAPH_VERSION
from pipeline.parts import PartResult
from pipeline.state import initial_state
from pipeline.tool_repair import VARIANTS
from validators import Severity

CASSETTE_DIR = DATASET_DIR.parent / "cassettes" / "repair"
UPLOADED_BY = "Eval runner"
REPORT_VERSION = 1


def cassette_path(variant: str, model: str, case_id: str, root: Path = CASSETTE_DIR):
    slug = "".join(c if c.isalnum() or c in "-." else "_" for c in model)
    return root / variant / slug / f"{case_id}.json"


@dataclass
class CaseResult:
    case: str
    fault: str
    split: str
    model: str
    variant: str
    repaired: bool = False
    repairs: int = 0
    errors_before: int = 0
    errors_after: int = 0
    unsafe_before: int = 0
    unsafe_after: int = 0
    stopped_at: str | None = None  # where routing would go next
    seconds: float = 0.0
    error: str | None = None  # an exception that ended the case
    log: list[dict] = field(default_factory=list)  # each part repair's RepairRecord


def _errors(state) -> int:
    return sum(f.severity is Severity.ERROR for f in state.get("findings", []))


def seeded_state(document_id: str, case) -> dict:
    """The state after a parse that carries the case's fault, before validate."""
    plan = parts.plan(case.pages, case.doc_type)
    pieces = split_parse(case.parse, plan)
    state = initial_state(
        document_id, uploaded_by=UPLOADED_BY, expected_patient=case.expected_patient
    )
    state.update(
        doc_type=case.doc_type,
        transcriptions=list(case.pages),
        part_results=[
            PartResult(
                index=p.index,
                reply=piece.model_dump_json(),
                parse=piece,
                model_role="primary",
            )  # fmt: skip
            for p, piece in zip(plan, pieces)
        ],
        parse=case.parse,
        parse_error=None,
        parse_attempts=1,
        parse_model_role="primary",
    )
    return state


def repair_case(rc, case, document_id: str, model: str, variant: str) -> CaseResult:
    """One case through the graph's repair and validate nodes."""
    result = CaseResult(rc.id, rc.fault, rc.split, model, variant)
    key = answer_key(rc.doc_id)
    start = time.monotonic()
    with start_run(document_id, GRAPH_VERSION) as run:
        state = seeded_state(document_id, case)
        state.update(nodes.validate(state))
        result.errors_before = _errors(state)
        result.unsafe_before = len(score_document(state["record"], key).unsafe)
        step = routing.after_validate(state)
        try:
            while step in ("repair", "validate"):
                node = nodes.repair if step == "repair" else nodes.validate
                state.update(node(state))
                step = (
                    routing.after_parse(state)
                    if node is nodes.repair
                    else routing.after_validate(state)
                )
        except CassetteMiss:
            raise
        except Exception as e:  # the case failed: nothing repaired
            result.error = type(e).__name__
        result.stopped_at = step
        result.repairs = state.get("repairs", 0)
        result.errors_after = _errors(state)
        result.repaired = result.error is None and result.errors_after == 0
        result.unsafe_after = len(score_document(state["record"], key).unsafe)
        result.log = [r.model_dump(mode="json") for r in state.get("repair_log", [])]
        run.finish("verified" if result.repaired else "needs_review")
    result.seconds = round(time.monotonic() - start, 1)
    return result


@contextmanager
def _as_primary(model: str, variant: str):
    """
    `model` as the primary (the other as the fallback a call may still
    reach on an error, as in the service), the variant set, validator 7's
    day fixed.
    """
    other = llm.FALLBACK_MODEL if model != llm.FALLBACK_MODEL else llm.PRIMARY_MODEL
    saved_models, saved_client, saved_today = (
        dict(llm._MODELS),
        llm._client,
        nodes.today,
    )
    saved_env = os.environ.get("MEDSUM_REPAIR")
    llm._MODELS.update({"primary": model, "fallback": other})
    os.environ["MEDSUM_REPAIR"] = variant
    nodes.today = lambda: TODAY
    try:
        yield
    finally:
        llm._MODELS.clear()
        llm._MODELS.update(saved_models)
        llm._client, nodes.today = saved_client, saved_today
        if saved_env is None:
            os.environ.pop("MEDSUM_REPAIR", None)
        else:
            os.environ["MEDSUM_REPAIR"] = saved_env
        model_health.reset()


def run_cases(
    cases: list,
    *,
    model: str,
    variant: str,
    user_id: str,
    client_for,
    after_case=lambda case_id: None,
) -> list[CaseResult]:
    results = []
    with _as_primary(model, variant):
        for rc in cases:
            case = repair_cases.case_parse(rc.id)
            model_health.reset()
            llm._client = client_for(rc.id)
            llm._digests.clear()
            document_id = insert_document(user_id, case.doc_id)
            results.append(repair_case(rc, case, document_id, model, variant))
            after_case(rc.id)
    return results


# -------------------------------------------------------------------
# Report
# -------------------------------------------------------------------
TOOL_COUNTS = ("tool_calls", "malformed", "unknown_tool", "not_found",
               "inexact_types", "over_limit")  # fmt: skip


def _mean(values) -> float | None:
    values = list(values)
    return round(statistics.fmean(values), 1) if values else None


def summarise(results: list[CaseResult]) -> dict:
    """One row: a model and variant over its cases."""
    logs = [r for c in results for r in c.log]
    repairs = sum(c.repairs for c in results)
    row = {
        "cases": len(results),
        "repaired": asdict(wilson(sum(c.repaired for c in results), len(results))),
        "repaired_first_try": sum(c.repaired and c.repairs == 1 for c in results),
        "failed_runs": sum(c.error is not None for c in results),
        "repairs": repairs,
        "repairs_per_case": round(repairs / len(results), 2) if results else None,
        "model_calls_per_repair": round(
            sum(r["model_calls"] for r in logs) / repairs, 2
        )
        if repairs
        else None,
        "tokens_per_repair": round(
            sum(r["prompt_tokens"] + r["completion_tokens"] for r in logs) / repairs
        )
        if repairs
        else None,
        "seconds_per_repair": round(sum(r["seconds"] for r in logs) / repairs, 1)
        if repairs
        else None,
        "median_seconds_per_case": round(
            statistics.median(c.seconds for c in results), 1
        )
        if results
        else None,
        "unsafe_before": sum(c.unsafe_before for c in results),
        "unsafe_after": sum(c.unsafe_after for c in results),
        "cases_with_unsafe_after": sum(c.unsafe_after > 0 for c in results),
    }
    if any(r["variant"] == "tools" for r in logs):
        by_tool: dict[str, int] = {}
        for r in logs:
            for name, n in r["by_tool"].items():
                by_tool[name] = by_tool.get(name, 0) + n
        tools = {name: sum(r[name] for r in logs) for name in TOOL_COUNTS}
        calls = tools["tool_calls"]
        tools.update(
            by_tool=dict(sorted(by_tool.items())),
            well_formed_rate=round(
                1 - (tools["malformed"] + tools["unknown_tool"]) / calls, 3
            )
            if calls
            else None,
            turns_per_repair=_mean(r["turns"] for r in logs),
            fix_in_reading_turn=sum(r["fix_turn"] == "reading" for r in logs),
            fix_in_final_turn=sum(r["fix_turn"] == "final" for r in logs),
            part_repairs=len(logs),
        )
        row["tools"] = tools
    return row


def by_fault(results: list[CaseResult]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in results:
        d = out.setdefault(c.fault, {"cases": 0, "repaired": 0})
        d["cases"] += 1
        d["repaired"] += c.repaired
    return dict(sorted(out.items()))


def verdict(rows: dict[str, dict]) -> dict:
    """
    Which variant one model's rows support. The tool repair only when it
    repairs at least as many cases, leaves no more unsafe errors and is not
    slower per repair than the fixed re-parse; the fixed one otherwise, or
    when either row is missing.
    """
    fixed, tools = rows.get("fixed"), rows.get("tools")
    if not fixed or not tools:
        return {"supports": "fixed", "why": ["both variants are needed to compare"]}
    reasons, ok = [], True
    f_rate, t_rate = fixed["repaired"]["hits"], tools["repaired"]["hits"]
    if t_rate < f_rate:
        ok = False
        reasons.append(f"tools repaired fewer cases ({t_rate} vs {f_rate})")
    if tools["unsafe_after"] > fixed["unsafe_after"]:
        ok = False
        reasons.append(
            f"tools left more unsafe errors ({tools['unsafe_after']} vs "
            f"{fixed['unsafe_after']})"
        )
    f_s, t_s = fixed["seconds_per_repair"], tools["seconds_per_repair"]
    if t_s is not None and f_s is not None and t_s > f_s:
        ok = False
        reasons.append(f"tools were slower per repair ({t_s} s vs {f_s} s)")
    if ok:
        reasons.append("tools repaired as many, no more unsafe errors, not slower")
    return {"supports": "tools" if ok else "fixed", "why": reasons}


def build_report(label: str, results: list[CaseResult], generated_at=None) -> dict:
    generated_at = generated_at or datetime.now(timezone.utc)
    models: dict[str, dict] = {}
    for model in dict.fromkeys(c.model for c in results):
        rows = {
            v: summarise([c for c in results if c.model == model and c.variant == v])
            for v in VARIANTS
            if any(c.model == model and c.variant == v for c in results)
        }
        faults = {
            v: by_fault([c for c in results if c.model == model and c.variant == v])
            for v in rows
        }
        models[model] = {"rows": rows, "by_fault": faults, "verdict": verdict(rows)}
    return {
        "version": REPORT_VERSION,
        "kind": "repair",
        "label": label,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "cases": sorted({c.case for c in results}),
        "models": models,
        "results": [asdict(c) for c in results],
    }


def render_markdown(report: dict) -> str:
    out = [f"# Repair eval: {report['label']}", "",
           f"Generated {report['generated_at']}; {len(report['cases'])} cases, "
           "each one injected fault that leaves an error finding.", ""]  # fmt: skip
    for model, m in report["models"].items():
        out += [f"## {model}", "", f"Supports: **{m['verdict']['supports']}** "
                f"({'; '.join(m['verdict']['why'])})", ""]  # fmt: skip
        out += ["| | " + " | ".join(m["rows"]) + " |",
                "|---|" + "---|" * len(m["rows"])]  # fmt: skip
        lines = [
            ("Cases repaired", lambda r: _rate(r["repaired"])),
            ("Repaired on the first repair", lambda r: str(r["repaired_first_try"])),
            ("Repairs per case", lambda r: _v(r["repairs_per_case"])),
            ("Model calls per repair", lambda r: _v(r["model_calls_per_repair"])),
            ("Tokens per repair", lambda r: _v(r["tokens_per_repair"])),
            ("Seconds per repair", lambda r: _v(r["seconds_per_repair"])),
            ("Median seconds per case", lambda r: _v(r["median_seconds_per_case"])),
            ("Unsafe errors before / after", lambda r: f"{r['unsafe_before']} / {r['unsafe_after']}"),
            ("Failed runs", lambda r: str(r["failed_runs"])),
        ]  # fmt: skip
        for name, cell in lines:
            out.append(
                f"| {name} | " + " | ".join(cell(r) for r in m["rows"].values()) + " |"
            )
        tools = m["rows"].get("tools", {}).get("tools")
        if tools:
            out += ["", "Tool calls (tool repair): " + ", ".join(
                f"{k} {v}" for k, v in tools.items() if k != "by_tool"
            ) + "; by tool: " + (", ".join(f"{k} {v}" for k, v in tools["by_tool"].items()) or "none"), ""]  # fmt: skip
        out += ["", "| Fault | " + " | ".join(m["by_fault"]) + " |",
                "|---|" + "---|" * len(m["by_fault"])]  # fmt: skip
        faults = sorted({f for v in m["by_fault"].values() for f in v})
        for f in faults:
            cells = [
                f"{v[f]['repaired']}/{v[f]['cases']}" if f in v else "-"
                for v in m["by_fault"].values()
            ]
            out.append(f"| {f} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out) + "\n"


def _rate(d: dict) -> str:
    if not d["total"]:
        return "0/0"
    return f"{d['hits']}/{d['total']} ({d['hits'] / d['total']:.0%}, CI {d['low']:.0%}-{d['high']:.0%})"


def _v(x) -> str:
    return "-" if x is None else str(x)


def write_report(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "report.json", out_dir / "report.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


# -------------------------------------------------------------------
# Command line
# -------------------------------------------------------------------
def select(subset: str, ids: str | None) -> list:
    cases = repair_cases.load()
    if ids:
        wanted = [i.strip() for i in ids.split(",")]
        unknown = [i for i in wanted if i not in cases]
        if unknown:
            raise SystemExit(f"unknown cases: {', '.join(unknown)}")
        return [cases[i] for i in wanted]
    return repair_cases.in_subset(cases, subset)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.repair")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="call Ollama, record")
    mode.add_argument("--replay", action="store_true", help="answer from cassettes")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--variant", choices=[*VARIANTS, "both"], default="both")
    parser.add_argument("--subset", choices=repair_cases.SUBSETS, default="core")
    parser.add_argument("--cases", help="comma-separated case ids")
    parser.add_argument("--label")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cassettes", type=Path, default=CASSETTE_DIR)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args(argv)

    models = args.models or [llm.PRIMARY_MODEL, llm.FALLBACK_MODEL]
    variants = list(VARIANTS) if args.variant == "both" else [args.variant]
    cases = select(args.subset, args.cases)
    mode = "live" if args.live else "replay"
    label = args.label or f"{time.strftime('%Y-%m-%d')}-repair-{mode}"
    live_client = llm._client
    results: list[CaseResult] = []
    with EvalDatabase() as db:
        for model in models:
            for variant in variants:
                recorded: dict[str, Cassette] = {}

                def client_for(
                    case_id, model=model, variant=variant, recorded=recorded
                ):
                    path = cassette_path(variant, model, case_id, args.cassettes)
                    if args.live:
                        recorded[case_id] = Cassette()
                        return RecordingClient(live_client, recorded[case_id])
                    if not path.exists():
                        raise CassetteMiss(f"{model}: no cassette {path}; re-record")
                    return ReplayClient(Cassette.load(path))

                def save(case_id, model=model, variant=variant, recorded=recorded):
                    if args.live:
                        recorded[case_id].save(
                            cassette_path(variant, model, case_id, args.cassettes)
                        )

                try:
                    out = run_cases(
                        cases, model=model, variant=variant, user_id=db.user_id,
                        client_for=client_for, after_case=save,
                    )  # fmt: skip
                except CassetteMiss as e:
                    print(f"FAIL replay: {e}", file=sys.stderr)
                    return 2
                results += out
                print(
                    f"{model} {variant}: {sum(r.repaired for r in out)}/{len(out)} repaired"
                )

    report = build_report(label, results)
    kept = args.live and not args.no_history
    name = history.run_name(label, report["generated_at"])
    out_dir = args.out or (history.REPORTS_DIR / name if kept else RESULTS_DIR / label)
    json_path, md_path = write_report(report, out_dir)
    print(f"wrote {json_path} and {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
