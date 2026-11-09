"""
The prompts the pipeline's nodes send, by name. The name and the text's
hash are recorded on every step that uses one (Step.set_prompt), so a
change to a prompt file shows up in the runs it affected.
"""

from functools import cache
from pathlib import Path

import config
from record.printed import DocumentType

PROMPT_DIR = Path(config.SCRIPTS_DIR) / "prompt"

FILES = {
    "extract_page": Path(config.EXTRACTION_PROMPT_FILE),
    "classify_document": PROMPT_DIR / "txt" / "classify_document.txt",
    "parse_lab_report": PROMPT_DIR / "txt" / "parse_lab_report.txt",
    "parse_prescription": PROMPT_DIR / "txt" / "parse_prescription.txt",
    "insight_prompt": Path(config.PROMPT_FILE),
}

PARSE_PROMPTS = {
    DocumentType.LAB_REPORT: "parse_lab_report",
    DocumentType.PRESCRIPTION: "parse_prescription",
}


@cache
def prompt(name: str) -> str:
    """A prompt's text. Read once per process; a changed file needs a restart."""
    return FILES[name].read_text(encoding="utf-8")
