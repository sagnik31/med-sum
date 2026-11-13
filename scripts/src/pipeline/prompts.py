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
    "repair_parse": PROMPT_DIR / "txt" / "repair_parse.txt",
}

PARSE_PROMPTS = {
    DocumentType.LAB_REPORT: "parse_lab_report",
    DocumentType.PRESCRIPTION: "parse_prescription",
}


@cache
def prompt(name: str) -> str:
    """A prompt's text. Read once per process; a changed file needs a restart."""
    return FILES[name].read_text(encoding="utf-8")


# -------------------------------------------------------------------
# Page transcription request
# -------------------------------------------------------------------
# The model ends each page with this line; generation stops there (Ollama's
# stop option), and nothing after it is kept.
END_MARKER = "[[END_OF_PAGE]]"
# Hard cap so a model stuck in a repetition loop cannot run forever; a real
# report page needs far fewer tokens. A page that hits it is capped (LC-02).
MAX_TOKENS_PER_PAGE = 4096


def transcription_messages(image_bytes: bytes, system_prompt: str) -> list[dict]:
    """The request that transcribes one page image."""
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Transcribe this page following your instructions. "
                f"At the very end, output {END_MARKER} on its own line, then stop."
            ),
            "images": [image_bytes],
        },
    ]
