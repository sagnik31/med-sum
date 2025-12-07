import os

# Resolve paths relative to this config file
# config.py is in <project>/scripts/src/config.py
SRC_DIR = os.path.dirname(os.path.abspath(__file__))  # .../scripts/src
SCRIPTS_DIR = os.path.dirname(SRC_DIR)  # .../scripts
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)  # .../med-sum

INPUT_PDF = os.path.join(PROJECT_ROOT, "data", "input", "txt", "medical_report.pdf")
OUTPUT_MD = os.path.join(
    PROJECT_ROOT, "data", "output", "extraction", "txt", "extracted_report_txt.md"
)
OUTPUT_MD_SLM = os.path.join(
    PROJECT_ROOT, "data", "output", "extraction", "txt", "extracted_report_slm.md"
)

# ------------- CONFIG -------------
# Markdown report to read (change this as needed)
INPUT_MD = os.path.join(
    PROJECT_ROOT, "data", "output", "extraction", "txt", "extracted_report_txt.md"
)
INPUT_MD_SLM = os.path.join(
    PROJECT_ROOT, "data", "output", "extraction", "txt", "extracted_report_slm.md"
)

# Prompt that defines how to turn markdown → HTML insights
# Prompts are in .../scripts/prompt/txt/
PROMPT_FILE = os.path.join(SCRIPTS_DIR, "prompt", "txt", "insight_prompt.txt")
VISION_PROMPT_FILE = os.path.join(SCRIPTS_DIR, "prompt", "img", "extract_report_img.txt")
OPTHAL_POINT_FILE = os.path.join(SCRIPTS_DIR, "prompt", "img", "opthal_report.txt")

# Text model to use for insights generation
MODEL_NAME = "qwen3:4b-instruct-2507-q8_0"

# Output HTML file
OUTPUT_HTML = os.path.join(
    PROJECT_ROOT, "data", "output", "insights", "txt", "report_insights_txt.html"
)
OUTPUT_HTML_SLM = os.path.join(
    PROJECT_ROOT, "data", "output", "insights", "txt", "report_insights_txt_slm.html"
)

PATIENT_SUMMARY_PROMPT_FILE = os.path.join(
    SCRIPTS_DIR, "prompt", "txt", "patient_summary_prompt.txt"
)
# ----------------------------------
