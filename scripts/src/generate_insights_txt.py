#!/usr/bin/env python

import os
import sys
from bs4 import BeautifulSoup  # used to ensure clean HTML structure


# Add parent directory to path to allow importing config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import INPUT_MD_SLM, OUTPUT_HTML_SLM, PRIMARY_MODEL, PROMPT_FILE
from llm import chat

# Hard cap so a model stuck in a repetition loop cannot run forever;
# real insights and patient summaries need far fewer tokens.
MAX_TOKENS = 8192
# Echo the report, prompt and streamed model output (patient data) to the
# console. Only the standalone CLI (main) turns this on; the service never does.
VERBOSE = False


# Minimal clean medical CSS styling
BASE_CSS = """
<style>
body {
  font-family: Arial, Helvetica, sans-serif;
  margin: 24px;
  line-height: 1.5;
  color: #222;
}
h2 {
  color: #013A63;
  margin-top: 28px;
  border-bottom: 2px solid #013A63;
  padding-bottom: 4px;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 12px;
}
table, th, td {
  border: 1px solid #C4C4C4;
}
th {
  background-color: #E8F1FA;
  text-align: left;
  padding: 8px;
}
td {
  padding: 8px;
  vertical-align: top;
}
ul {
  margin-left: 22px;
}
</style>
"""


def load_text(path: str) -> str:
    """
    Raises FileNotFoundError instead of calling sys.exit so that callers
    (like the FastAPI worker) can handle the error gracefully.
    """
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def generate_insights(prompt: str, markdown_report: str) -> str:
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": markdown_report},
    ]

    if VERBOSE:
        print(f"\n=== 📝 Loading Markdown Report === \n{markdown_report}")
        print(f"\n=== 📝 Loading Prompt === \n{prompt}")
    print("\n=== 💡 Generating Clinical Insights (streaming) ===\n")
    print("\n=== Model Name===\n", PRIMARY_MODEL)

    streamed = chat(messages, max_tokens=MAX_TOKENS, verbose=VERBOSE)

    print("\n\n=== 🚀 Generation complete ===\n")
    return streamed


def sanitize_and_wrap_html(inner_html: str) -> str:
    """
    The model already outputs HTML body; we wrap + clean using BeautifulSoup.
    """
    soup = BeautifulSoup(inner_html, "html.parser")

    # Ensure valid HTML document structure
    html_doc = f"""
<html>
<head>
<meta charset="UTF-8">
<title>Report Insights</title>
{BASE_CSS}
</head>
<body>
{soup}
</body>
</html>
"""
    return html_doc


# -------------------------------------------------------------------
# NEW: reusable wrapper so other Python code can call this directly
# -------------------------------------------------------------------
def generate_insights_html(markdown_data: str, prompt: str | None = None) -> str:
    """
    Takes extracted Markdown text and returns a full HTML document string
    (with <html>...</html> and CSS).
    """
    if prompt is None:
        prompt = load_text(PROMPT_FILE)

    inner_html_raw = generate_insights(prompt, markdown_data)
    final_html = sanitize_and_wrap_html(inner_html_raw)
    return final_html


def main():
    global VERBOSE
    VERBOSE = True
    prompt = load_text(PROMPT_FILE)
    markdown_data = load_text(INPUT_MD_SLM)

    inner_html_raw = generate_insights(prompt, markdown_data)
    final_html = sanitize_and_wrap_html(inner_html_raw)

    with open(OUTPUT_HTML_SLM, "w", encoding="utf-8") as f:
        f.write(final_html)

    print(f"📄 Saved insights ➜ {os.path.abspath(OUTPUT_HTML_SLM)}")
    print("✔ Styled, valid HTML document ready for browser view!")


if __name__ == "__main__":
    main()
