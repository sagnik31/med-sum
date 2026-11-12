"""
Check the eval harness end to end without a model: score every answer key
and expected transcription against itself, route each key through the
validators and signals (as the fault check does), and write the report.
Every score should be perfect and every document should end as the
manifest expects; anything else is a bug in the scorers or the dataset.

    python -m evals.selfcheck [OUT_DIR]

OUT_DIR defaults to scripts/evals/results/selfcheck (gitignored).
"""

import sys
from pathlib import Path

from dataset.build import DATASET_DIR
from evals.faultcheck import clean_case, outcome
from evals.keys import answer_key, documents, manifest
from evals.report import DocumentResult, ModelRun, build_report, write_report
from evals.score import score_document
from evals.transcription import score_transcription

RESULTS_DIR = DATASET_DIR.parent / "results"
LABEL = "answer keys"


def selfcheck_run(dataset_dir: Path = DATASET_DIR) -> ModelRun:
    run = ModelRun(LABEL)
    for doc_id in documents(dataset_dir):
        key = answer_key(doc_id, dataset_dir)
        pages = answer_key(doc_id, dataset_dir).pages
        run.documents.append(
            DocumentResult(
                doc_id,
                score_document(answer_key(doc_id, dataset_dir), key),
                score_transcription(pages, key),
            )
        )
    return run


def selfcheck_outcomes(dataset_dir: Path = DATASET_DIR) -> dict[str, dict]:
    """Each answer key's end state and routing signals."""
    out = {}
    for doc_id in documents(dataset_dir):
        o = outcome(clean_case(doc_id, dataset_dir))
        out[doc_id] = {
            "end_state": o.end_state,
            "signals": o.routing_ids(),
            "record_model_role": None,
            "error": None,
        }
    return out


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    out_dir = Path(argv[0]) if argv else RESULTS_DIR / "selfcheck"
    report = build_report(
        "self-check",
        [selfcheck_run()],
        manifest(),
        outcomes={LABEL: selfcheck_outcomes()},
    )
    json_path, md_path = write_report(report, out_dir)
    print(f"wrote {json_path} and {md_path}")
    choice = report["choice"]
    print(f"model choice: {choice['model']} ({choice['reason']})")


if __name__ == "__main__":
    main()
