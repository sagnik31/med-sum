"""
Check the eval harness end to end without a model: score every answer key
and expected transcription against itself and write the report. Every
number should be perfect; anything else is a bug in the scorers or the
dataset.

    python -m evals.selfcheck [OUT_DIR]

OUT_DIR defaults to scripts/evals/results/selfcheck (gitignored).
"""

import sys
from pathlib import Path

from dataset.build import DATASET_DIR
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


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    out_dir = Path(argv[0]) if argv else RESULTS_DIR / "selfcheck"
    report = build_report("self-check", [selfcheck_run()], manifest())
    json_path, md_path = write_report(report, out_dir)
    print(f"wrote {json_path} and {md_path}")
    choice = report["choice"]
    print(f"model choice: {choice['model']} ({choice['reason']})")


if __name__ == "__main__":
    main()
