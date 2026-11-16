"""python -m demo: see demo/__init__.py."""

import argparse
import sys
from pathlib import Path

from demo import DOCUMENTS, run
from evals import run as runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo",
        description="Two dataset documents through the real graph, from cassettes.",
    )
    parser.add_argument(
        "--model", help="the primary model (default: the configured one)"
    )
    parser.add_argument("--docs", default=",".join(DOCUMENTS),
                        help="comma-separated dataset ids")  # fmt: skip
    parser.add_argument("--cassettes", type=Path, default=runner.CASSETTE_DIR)
    args = parser.parse_args(argv)
    doc_ids = runner.select(args.docs, None)
    return run(args.model, doc_ids, args.cassettes)


if __name__ == "__main__":
    sys.exit(main())
