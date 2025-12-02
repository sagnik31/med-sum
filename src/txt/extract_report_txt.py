#!/usr/bin/env python

import os
import sys
import re
import fitz  # PyMuPDF

# Add parent directory to path to allow importing config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from config import INPUT_PDF, OUTPUT_MD


def extract_lines_from_pdf(pdf_path: str):
    """Return list of pages, each as list of lines (strings)."""
    if not os.path.exists(pdf_path):
        print(f"[ERROR] PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    doc = fitz.open(pdf_path)
    pages = []
    for page in doc:
        # Use simple text extraction, then split into lines
        text = page.get_text("text")
        lines = text.splitlines()
        pages.append(lines)
    return pages


def is_heading(line: str) -> bool:
    """Heuristic: consider a line a heading if:
    - It's fairly short
    - Mostly uppercase OR ends with ':'"""
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) > 60:  # too long to be a heading usually
        return False

    # Ends with colon often used as section header
    if stripped.endswith(":"):
        return True

    # Mostly uppercase and letters / spaces
    letters = re.sub(r"[^A-Za-z]", "", stripped)
    if not letters:
        return False

    upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper_ratio > 0.7


def looks_like_table_block(lines):
    """
    Heuristic: a block looks like a table if
    - It has at least 3 non-empty lines
    - Many lines have multiple chunks separated by 2+ spaces
    """
    non_empty = [ln for ln in lines if ln.strip()]
    if len(non_empty) < 3:
        return False

    multi_col_count = 0
    for ln in non_empty:
        # Split on 2+ spaces
        parts = re.split(r"\s{2,}", ln.strip())
        if len(parts) >= 2:
            multi_col_count += 1

    # If most lines have 2+ columns, treat as table
    return multi_col_count >= max(2, len(non_empty) // 2)


def block_to_markdown_table(lines):
    """
    Convert a block of lines into a markdown table using 2+ spaces as column separators.
    First line is assumed to be header.
    """
    # Split each non-empty line into columns
    rows = []
    for ln in lines:
        if not ln.strip():
            continue
        cols = re.split(r"\s{2,}", ln.strip())
        rows.append(cols)

    if not rows:
        return ""

    # Normalize column counts by padding with empty strings
    max_cols = max(len(r) for r in rows)
    norm_rows = [r + [""] * (max_cols - len(r)) for r in rows]

    header = norm_rows[0]
    data_rows = norm_rows[1:]

    md_lines = []
    # Header
    md_lines.append("| " + " | ".join(header) + " |")
    # Separator
    md_lines.append("|" + "|".join(["---"] * max_cols) + "|")
    # Rows
    for r in data_rows:
        md_lines.append("| " + " | ".join(r) + " |")

    return "\n".join(md_lines)


def group_blocks(lines):
    """
    Group consecutive lines into blocks separated by empty lines.
    Each block is a list of lines.
    """
    blocks = []
    current = []
    for ln in lines:
        if ln.strip():
            current.append(ln)
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)
    return blocks


def page_to_markdown(lines, page_number: int):
    """
    Convert a single page's lines into markdown:
    - 'Page N' header
    - Blocks as headings, tables, or paragraphs based on heuristics
    """
    md = []
    md.append(f"## Page {page_number}")
    md.append("")

    blocks = group_blocks(lines)

    for block in blocks:
        # Single-line block that looks like a heading
        if len(block) == 1 and is_heading(block[0]):
            heading_text = block[0].strip().rstrip(":")
            # Capitalize nicely
            heading_text = heading_text.title()
            md.append(f"### {heading_text}")
            md.append("")
            continue

        # Multi-line block that looks like table
        if looks_like_table_block(block):
            md.append(block_to_markdown_table(block))
            md.append("")
            continue

        # Otherwise, treat as paragraphs
        # Collapse lines into one paragraph, but keep block boundaries
        para = " ".join(ln.strip() for ln in block)
        md.append(para)
        md.append("")

    return "\n".join(md)


def pdf_to_markdown(pdf_path: str) -> str:
    pages = extract_lines_from_pdf(pdf_path)
    md_pages = []
    for i, lines in enumerate(pages, start=1):
        md = page_to_markdown(lines, i)
        md_pages.append(md)
    return "\n\n".join(md_pages)


def main():
    pdf_path = INPUT_PDF
    if not os.path.exists(pdf_path):
        print(f"[ERROR] PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    print("\n=== 📄 Converting PDF to Markdown (no LLM) ===\n")
    markdown = pdf_to_markdown(pdf_path)

    # Stream to console
    print(markdown)

    # Save to file
    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write(markdown)

    print("\n=== ✅ Markdown saved ===")
    print(os.path.abspath(OUTPUT_MD))


if __name__ == "__main__":
    main()
