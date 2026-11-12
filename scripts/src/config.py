import os

# Resolve paths relative to this config file
# config.py is in <project>/scripts/src/config.py
SRC_DIR = os.path.dirname(os.path.abspath(__file__))  # .../scripts/src
SCRIPTS_DIR = os.path.dirname(SRC_DIR)  # .../scripts
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)  # .../med-sum

# The document the extraction script transcribes when run on its own: a
# synthetic lab report from the dataset (scripts/evals/dataset/).
SAMPLE_DOCUMENT = os.path.join(
    SCRIPTS_DIR, "evals", "dataset", "documents", "d09_lab_report.pdf"
)
OUTPUT_MD_SLM = os.path.join(
    PROJECT_ROOT, "data", "output", "extraction", "txt", "extracted_report_slm.md"
)

# Page transcription prompt, for lab reports and prescriptions
EXTRACTION_PROMPT_FILE = os.path.join(SCRIPTS_DIR, "prompt", "img", "extract_page.txt")

# Local models served by Ollama. Both read images and text. The primary does
# all the work; the fallback gets the same request when the primary errors or
# runs out of tokens. Environment variables override them (useful for evals).
PRIMARY_MODEL = os.environ.get("MEDSUM_PRIMARY_MODEL", "qwen3.5:4b")
FALLBACK_MODEL = os.environ.get("MEDSUM_FALLBACK_MODEL", "gemma4:e2b")

# Context window per model call. Ollama's default (4096 tokens) is smaller
# than one 200-DPI page image plus the prompt, and Ollama rejects requests
# that do not fit.
NUM_CTX = int(os.environ.get("MEDSUM_NUM_CTX", "16384"))

# Structured JSON log file (observability.configure_logging). Logs hold ids,
# statuses and timings only, never patient content; it is gitignored.
LOG_FILE = os.environ.get(
    "MEDSUM_LOG_FILE", os.path.join(SCRIPTS_DIR, "logs", "medsum.jsonl")
)

# ----------------------------------
