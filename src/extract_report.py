#!/usr/bin/env python

import os
import sys
import ollama
import fitz  # PyMuPDF for PDF text extraction


# ------------ CONFIG ------------
# Set this to the file you want to process:
# e.g. "medical_report.jpg" or "medical_report.pdf"
INPUT_FILE = "medical_report.pdf"

# Models
IMG_MODEL = "qwen2.5vl:7b"                      # for image OCR → markdown
TXT_MODEL = "qwen3:4b-instruct-2507-q8_0"       # for PDF text → markdown

# Prompts
IMG_PROMPT_FILE = "extract_report_img.txt"      # vision prompt (with [[END_OF_REPORT]])
TXT_PROMPT_FILE = "extract_report_txt.txt"      # text-PDF → markdown prompt

# Output files
IMG_OUTPUT_FILE = "extracted_report_img.md"
TXT_OUTPUT_FILE = "extracted_report_txt.md"

# Marker to stop vision model looping
END_MARKER = "[[END_OF_REPORT]]"
# --------------------------------


def load_prompt(path: str) -> str:
    if not os.path.exists(path):
        print(f"\n[ERROR] Prompt file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def run_model_stream(messages, model: str, use_end_marker: bool = False) -> str:
    """
    Run an Ollama chat call with streaming, printing tokens as they arrive.

    If use_end_marker=True, we:
    - pass END_MARKER as a stop sequence
    - strip everything after END_MARKER from the final output.
    """
    full = ""

    options = {}
    if use_end_marker:
        options["stop"] = [END_MARKER]
        # You can also add num_predict here if you want:
        # options["num_predict"] = 2048

    for chunk in ollama.chat(
        model=model,
        messages=messages,
        stream=True,
        options=options,
    ):
        token = chunk.get("message", {}).get("content", "")
        if not token:
            continue

        full += token
        print(token, end="", flush=True)

        if use_end_marker and END_MARKER in full:
            break

    print("\n")  # end of stream

    if use_end_marker and END_MARKER in full:
        full = full.split(END_MARKER)[0]

    return full


def extract_from_image(input_path: str):
    """
    For image input:
    - Load vision prompt
    - Call VLM (with END_MARKER)
    - Stream markdown to console
    - Save to extracted_report_img.md
    """
    system_prompt = load_prompt(IMG_PROMPT_FILE)

    print(f"\n=== ⬇️ Using Vision Model to OCR image: {input_path} ===\n")
    with open(input_path, "rb") as f:
        image_bytes = f.read()

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Extract this medical report image and return ONLY its content "
                "in valid Markdown format exactly as printed."
            ),
            "images": [image_bytes],
        },
    ]

    content = run_model_stream(messages, IMG_MODEL, use_end_marker=True)

    with open(IMG_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print("📄 Image-based markdown report saved →")
    print(os.path.abspath(IMG_OUTPUT_FILE))


def extract_raw_text_from_pdf(pdf_path: str) -> str:
    """
    Use PyMuPDF to extract raw text from a text-based PDF.
    No SLM here, just plain extraction.
    """
    print(f"\n=== 📄 Extracting raw text from PDF: {pdf_path} ===\n")
    doc = fitz.open(pdf_path)
    parts = []
    for page_num, page in enumerate(doc, start=1):
        page_text = page.get_text()
        parts.append(f"Page {page_num}:\n{page_text}")
    return "\n\n".join(parts)


def reformat_pdf_text_to_markdown(pdf_text: str) -> str:
    """
    Use the text model + extract_report_txt.txt prompt to turn
    the raw PDF text into clean Markdown that mirrors the original report.
    """
    system_prompt = load_prompt(TXT_PROMPT_FILE)

    print("\n=== 🧠 Reformatting PDF text → Markdown (Text Model) ===\n")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": pdf_text},
    ]

    # No END_MARKER here unless you also add it to the text prompt.
    content = run_model_stream(messages, TXT_MODEL, use_end_marker=False)
    return content


def extract_from_pdf(input_path: str):
    """
    For PDF input:
    - Extract raw text via PyMuPDF
    - Send that text to the text model with extract_report_txt.txt
    - Stream markdown to console
    - Save to extracted_report_txt.md
    """
    pdf_text = extract_raw_text_from_pdf(input_path)
    md_content = reformat_pdf_text_to_markdown(pdf_text)

    with open(TXT_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(md_content)

    print("📄 Text-PDF-based markdown report saved →")
    print(os.path.abspath(TXT_OUTPUT_FILE))


def main():
    input_path = INPUT_FILE

    if not os.path.exists(input_path):
        print(f"\n[ERROR] Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(input_path)[1].lower()

    print("\n======================================")
    print(f" Processing: {input_path}")
    print("======================================\n")

    if ext in [".jpg", ".jpeg", ".png"]:
        extract_from_image(input_path)
    elif ext == ".pdf":
        extract_from_pdf(input_path)
    else:
        print(f"[ERROR] Unsupported file type: {ext}", file=sys.stderr)
        sys.exit(1)

    print("\n🎯 DONE — Report extracted successfully!\n")


if __name__ == "__main__":
    main()