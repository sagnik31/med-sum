INPUT_PDF = "input/txt/medical_report.pdf"
OUTPUT_MD = "output/extraction/txt/extracted_report_txt.md"
OUTPUT_MD_SLM = "output/extraction/txt/extracted_report_slm.md"

# ------------- CONFIG -------------
# Markdown report to read (change this as needed)
INPUT_MD = (
    "output/extraction/txt/extracted_report_txt.md"  # or "extracted_report_img.md"
)
INPUT_MD_SLM = (
    "output/extraction/txt/extracted_report_slm.md"  # or "extracted_report_img.md"
)

# Prompt that defines how to turn markdown → HTML insights
PROMPT_FILE = "prompt/txt/insight_prompt.txt"

VISION_PROMPT_FILE = "prompt/txt/extract_report.txt"

# Text model to use for insights generation
MODEL_NAME = "qwen3:4b-instruct-2507-q8_0"

# Output HTML file
OUTPUT_HTML = "output/insights/txt/report_insights_txt.html"
OUTPUT_HTML_SLM = "output/insights/txt/report_insights_txt_slm.html"
# ----------------------------------
