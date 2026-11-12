"""
The pipeline's prompts must not teach the model the eval dataset. An
example copied from a dataset document (a patient's name, a date, a
clinician) would let the model pass that document by recall, and every
score on it would be worth less.

Two checks, over the prompts whose output the evals score:
- no line of a prompt is a line of any expected transcription;
- no quoted example of MIN_EXAMPLE_CHARS or more appears in any
  transcription line. Shorter quotes are notation the prompt has to teach
  (flags like "H", frequencies like "1-0-1" or "BD", forms like "Tab."),
  which every document shares, and the schema's own words (a remark's
  scope "document", a condition's kind "diagnosis") are not examples.

Text is compared after NFKC and whitespace collapse, case ignored.
"""

import re
import unicodedata

import pytest

from dataset.build import DATASET_DIR
from dataset_records import DOCS
from pipeline.prompts import prompt
from record.printed import ConditionKind, DocumentType, RemarkKind, RemarkScope

SCORED_PROMPTS = [
    "extract_page",
    "classify_document",
    "parse_lab_report",
    "parse_prescription",
]
MIN_EXAMPLE_CHARS = 8
SCHEMA_WORDS = {
    e.value
    for kind in (ConditionKind, DocumentType, RemarkKind, RemarkScope)
    for e in kind
}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


def transcription_lines() -> set[str]:
    lines = set()
    for doc in DOCS.values():
        text = (DATASET_DIR / doc["transcription"]).read_text(encoding="utf-8")
        lines.update(n for n in map(norm, text.splitlines()) if n)
    return lines


LINES = transcription_lines()


def quoted_examples(text: str) -> list[str]:
    return [
        q
        for q in re.findall(r'"([^"\n]+)"', text)
        if len(norm(q)) >= MIN_EXAMPLE_CHARS and q not in SCHEMA_WORDS
    ]


@pytest.mark.parametrize("name", SCORED_PROMPTS)
def test_no_prompt_line_is_a_dataset_line(name):
    shared = {n for n in map(norm, prompt(name).splitlines()) if n} & LINES
    assert not shared


@pytest.mark.parametrize("name", SCORED_PROMPTS)
def test_no_prompt_example_comes_from_the_dataset(name):
    found = [q for q in quoted_examples(prompt(name))
             if any(norm(q) in line for line in LINES)]  # fmt: skip
    assert not found


def test_the_check_would_catch_an_example_copied_from_the_dataset():
    copied = 'From "Patient Name: Ms. Fatima Shaikh" the name is "Ms. Fatima Shaikh".'
    assert [q for q in quoted_examples(copied)
            if any(norm(q) in line for line in LINES)]  # fmt: skip
