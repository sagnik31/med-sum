"""
Unified interface for document → markdown → insights
"""

from extract_report_slm import extract_markdown_from_file
from generate_insights_txt import generate_insights_html


def process_document(input_path: str) -> tuple[str, str]:
    """
    1️⃣ Extract Markdown via VLM
    2️⃣ Generate full HTML insights
    Returns: (markdown, html)
    """
    markdown = extract_markdown_from_file(input_path)
    html = generate_insights_html(markdown)
    return markdown, html