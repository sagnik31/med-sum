#!/usr/bin/env python

import os
import sys
import ollama

# ====== FIXED CONFIG (no CLI args expected) ======
INPUT_MD_FILE = "extracted_report_img.md"    # Markdown extracted by Stage 1
PROMPT_FILE = "text_prompt.txt"       # Updated prompt for HTML insights
OUTPUT_HTML_FILE = "report_insights.html"
MODEL_NAME = "qwen3:4b-instruct-2507-q8_0"
# =================================================


def load_file(path: str) -> str:
    if not os.path.exists(path):
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as file:
        return file.read().strip()


def generate_insights(prompt: str, markdown_report: str) -> str:
    print("\n=== Generating HTML Insights (streaming) ===\n")
    full_output = ""

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": markdown_report},
    ]

    for chunk in ollama.chat(model=MODEL_NAME, messages=messages, stream=True):
        token = chunk.get("message", {}).get("content", "")
        if token:
            print(token, end="", flush=True)
            full_output += token

    print("\n\n=== Insights Complete ===\n")
    return full_output


def main():
    prompt = load_file(PROMPT_FILE)
    markdown_report = load_file(INPUT_MD_FILE)

    html_output = generate_insights(prompt, markdown_report)

    with open(OUTPUT_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html_output)

    print("======= INSIGHTS HTML SAVED =======")
    print("File:", os.path.abspath(OUTPUT_HTML_FILE))


if __name__ == "__main__":
    main()