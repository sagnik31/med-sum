#!/usr/bin/env python


import os
import sys

# Optional: only needed for PDF → image conversion
import fitz  # PyMuPDF


# ------------- CONFIG -------------
# Add parent directory to path to allow importing config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import (
    OUTPUT_MD_SLM,
    INPUT_PDF,
    OPTHAL_POINT_FILE,
    PRIMARY_MODEL,
)  # INPUT_PDF is generic input file
from llm import chat

END_MARKER = "[[END_OF_PAGE]]"
# Hard cap so a model stuck in a repetition loop cannot run forever;
# a real report page needs far fewer tokens.
MAX_TOKENS_PER_PAGE = 4096
# Echo prompts and streamed model output (patient data) to the console.
# Only the standalone CLI (main) turns this on; the service never does.
VERBOSE = False
# ----------------------------------


def load_prompt(path: str) -> str:
    """
    Load the system prompt for the VLM.

    Raises FileNotFoundError instead of calling sys.exit so that callers
    (like the FastAPI worker) can handle the error gracefully.
    """
    if not os.path.exists(path):
        print(f"[ERROR] Prompt file not found: {path}", file=sys.stderr)
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def run_vlm_on_image_bytes(image_bytes: bytes, system_prompt: str, label: str) -> str:
    """
    Transcribe a single image (JPG/PNG or PDF-rendered page) given as bytes,
    using the primary model with the fallback if needed (see llm.chat).
    Stops when END_MARKER is seen and returns the content WITHOUT it.
    """
    print(f"\n=== 👁️ Processing {label} with vision model ===\n")

    if VERBOSE:
        print(f"SYSTEM PROMPT: {system_prompt}")

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

    page_md = chat(
        messages,
        max_tokens=MAX_TOKENS_PER_PAGE,
        stop_marker=END_MARKER,
        verbose=VERBOSE,
    )

    print(f"\n\n=== ✅ Finished {label} ===\n")
    return page_md


def process_image_file(input_path: str, system_prompt: str) -> str:
    """Read a normal image file and send it to the VLM."""
    with open(input_path, "rb") as f:
        image_bytes = f.read()
    page_md = run_vlm_on_image_bytes(
        image_bytes, system_prompt, label=os.path.basename(input_path)
    )
    return page_md


def process_pdf_file(input_path: str, system_prompt: str, on_page=None) -> str:
    """
    Render each PDF page to an image in memory and process via VLM.
    Concatenate all page markdown outputs.
    `on_page`, if given, is called before each page (progress heartbeat).
    """
    all_pages_md = []

    # Closed even on failure: an open handle keeps the file locked on
    # Windows, so the backend could not delete it.
    with fitz.open(input_path) as doc:
        num_pages = len(doc)
        print(f"\n=== 📄 PDF detected: {num_pages} page(s) ===")

        for i, page in enumerate(doc, start=1):
            if on_page:
                on_page()
            label = f"{os.path.basename(input_path)} - page {i}/{num_pages}"
            print(f"\n--- Rendering {label} to image ---")

            pix = page.get_pixmap(dpi=200)
            image_bytes = pix.tobytes("png")

            page_md = run_vlm_on_image_bytes(image_bytes, system_prompt, label=label)
            all_pages_md.append(page_md)

    return "\n\n".join(md for md in all_pages_md if md)


# -------------------------------------------------------------------
# Reusable wrapper so other Python code can call this directly
# -------------------------------------------------------------------
def extract_markdown_from_file(input_path: str, on_page=None) -> str:
    """
    Given a local file path (PDF / JPG / PNG / etc.), run the vision pipeline
    and return the extracted Markdown string.
    `on_page`, if given, is called before each PDF page (progress heartbeat).
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    system_prompt = load_prompt(OPTHAL_POINT_FILE)
    ext = os.path.splitext(input_path)[1].lower()

    print("\n======================================")
    print(f" Vision-based extraction for: {input_path}")
    print(" Using model:", PRIMARY_MODEL)
    print("======================================\n")

    if ext in [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"]:
        full_md = process_image_file(input_path, system_prompt)
    elif ext == ".pdf":
        full_md = process_pdf_file(input_path, system_prompt, on_page=on_page)
    else:
        raise ValueError(f"Unsupported file type for vision model: {ext}")

    return full_md


def main():
    global VERBOSE
    VERBOSE = True
    input_path = INPUT_PDF  # generic input file path from config

    if not os.path.exists(input_path):
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    full_md = extract_markdown_from_file(input_path)

    # Save final Markdown
    with open(OUTPUT_MD_SLM, "w", encoding="utf-8") as f:
        f.write(full_md)

    print("📄 Full Markdown report saved →")
    print(os.path.abspath(OUTPUT_MD_SLM))
    print("\n🎯 DONE — High-quality vision-based extraction complete.\n")


if __name__ == "__main__":
    main()
