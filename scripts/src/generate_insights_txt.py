#!/usr/bin/env python

import os
import sys
import ollama
from bs4 import BeautifulSoup  # used to ensure clean HTML structure


# Add parent directory to path to allow importing config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import INPUT_MD_SLM, OUTPUT_HTML_SLM, MODEL_NAME, PROMPT_FILE


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
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def generate_insights(prompt: str, markdown_report: str) -> str:
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": markdown_report},
    ]

    print("\n=== 💡 Generating Clinical Insights (streaming) ===\n")

    streamed = ""
    for chunk in ollama.chat(
        model=MODEL_NAME,
        messages=messages,
        stream=True,
    ):
        token = chunk.get("message", {}).get("content", "")
        if not token:
            continue
        streamed += token
        print(token, end="", flush=True)

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
