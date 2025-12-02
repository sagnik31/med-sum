#!/usr/bin/env python

import os
import sys
import ollama

# Optional: only needed for PDF → image conversion
import fitz  # PyMuPDF


# ------------- CONFIG -------------
# Add parent directory to path to allow importing config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import OUTPUT_MD_SLM, INPUT_PDF, VISION_PROMPT_FILE  # INPUT_PDF is generic input file

VISION_MODEL = "qwen2.5vl:7b"
END_MARKER = "[[END_OF_PAGE]]"
# ----------------------------------


def load_prompt(path: str) -> str:
    if not os.path.exists(path):
        print(f"[ERROR] Prompt file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def run_vlm_on_image_bytes(image_bytes: bytes, system_prompt: str, label: str) -> str:
    """
    Call qwen2.5vl:7b on a single image (JPG/PNG or PDF-rendered page) as bytes.
    Streams output to console and stops when END_MARKER is seen.
    Returns the content WITHOUT the END_MARKER.
    """
    print(f"\n=== 👁️ Processing {label} with vision model ===\n")

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Extract ONLY the essential clinical content from this report page as per your instructions: "
                "lab/test tables and clinician remarks, in Markdown. "
                "At the very end, output [[END_OF_PAGE]] on its own line, then stop."
            ),
            "images": [image_bytes],
        },
    ]

    full = ""
    for chunk in ollama.chat(
        model=VISION_MODEL,
        messages=messages,
        stream=True,
    ):
        token = chunk.get("message", {}).get("content", "")
        if not token:
            continue
        full += token
        print(token, end="", flush=True)

        if END_MARKER in full:
            break

    print(f"\n\n=== ✅ Finished {label} ===\n")

    if END_MARKER in full:
        full = full.split(END_MARKER)[0]

    return full.strip()


def process_image_file(input_path: str, system_prompt: str) -> str:
    """Read a normal image file and send it to the VLM."""
    with open(input_path, "rb") as f:
        image_bytes = f.read()
    page_md = run_vlm_on_image_bytes(image_bytes, system_prompt, label=os.path.basename(input_path))
    return page_md


def process_pdf_file(input_path: str, system_prompt: str) -> str:
    """
    Render each PDF page to an image in memory and process via VLM.
    Concatenate all page markdown outputs.
    """
    doc = fitz.open(input_path)
    all_pages_md = []

    num_pages = len(doc)
    print(f"\n=== 📄 PDF detected: {num_pages} page(s) ===")

    for i, page in enumerate(doc, start=1):
        label = f"{os.path.basename(input_path)} - page {i}/{num_pages}"
        print(f"\n--- Rendering {label} to image ---")

        pix = page.get_pixmap(dpi=200)
        image_bytes = pix.tobytes("png")

        page_md = run_vlm_on_image_bytes(image_bytes, system_prompt, label=label)
        all_pages_md.append(page_md)

    return "\n\n".join(md for md in all_pages_md if md)


def main():
    input_path = INPUT_PDF  # generic input file path from config

    if not os.path.exists(input_path):
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    system_prompt = load_prompt(VISION_PROMPT_FILE)
    ext = os.path.splitext(input_path)[1].lower()

    print("\n======================================")
    print(f" Vision-based extraction for: {input_path}")
    print(" Using model:", VISION_MODEL)
    print("======================================\n")

    if ext in [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"]:
        full_md = process_image_file(input_path, system_prompt)
    elif ext == ".pdf":
        full_md = process_pdf_file(input_path, system_prompt)
    else:
        print(f"[ERROR] Unsupported file type for vision model: {ext}", file=sys.stderr)
        sys.exit(1)

    # Save final Markdown
    with open(OUTPUT_MD_SLM, "w", encoding="utf-8") as f:
        f.write(full_md)

    print("📄 Full Markdown report saved →")
    print(os.path.abspath(OUTPUT_MD_SLM))
    print("\n🎯 DONE — High-quality vision-based extraction complete.\n")


if __name__ == "__main__":
    main()
