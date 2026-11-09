"""
A document file's pages: how many there are, each page as the image the
vision model is sent, and each PDF page's own text layer.

PDF pages are rendered at RENDER_DPI. A JPG or PNG is one page, sent as
its own bytes. Only the image's hash, size and dimensions go into the
graph state (PageImage); the transcribe node renders the page again, and
the hash shows it is the same page that was counted.
"""

import hashlib
from pathlib import Path

import pymupdf

from pipeline.state import PageImage

RENDER_DPI = 200
# Longest document accepted. Each page is a model call that takes minutes
# on a CPU.
MAX_PAGES = 20
IMAGE_TYPES = {".jpg", ".jpeg", ".png"}
SUPPORTED_TYPES = IMAGE_TYPES | {".pdf"}


class DocumentFileError(ValueError):
    """The file can't be processed: missing, unsupported, too long or unreadable."""


def _kind(path: Path) -> str:
    if not path.is_file():
        raise DocumentFileError("the document's file is missing")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        raise DocumentFileError(f"unsupported file type {suffix or '(none)'}")
    return "image" if suffix in IMAGE_TYPES else "pdf"


def _open_pdf(path: Path):
    try:
        return pymupdf.open(path)
    except Exception as e:  # PyMuPDF raises several types for a bad file
        raise DocumentFileError(f"the PDF can't be read ({type(e).__name__})") from e


def page_count(path: str | Path) -> int:
    path = Path(path)
    if _kind(path) == "image":
        return 1
    with _open_pdf(path) as pdf:
        n = len(pdf)
    if n == 0:
        raise DocumentFileError("the PDF has no pages")
    if n > MAX_PAGES:
        raise DocumentFileError(f"the PDF has {n} pages; the limit is {MAX_PAGES}")
    return n


def page_png(path: str | Path, page: int) -> bytes:
    """The image of page `page` (1-based) as sent to the vision model."""
    path = Path(path)
    if _kind(path) == "image":
        if page != 1:
            raise DocumentFileError(f"an image has one page, not page {page}")
        return path.read_bytes()
    with _open_pdf(path) as pdf:
        if not 1 <= page <= len(pdf):
            raise DocumentFileError(f"the PDF has no page {page}")
        return pdf[page - 1].get_pixmap(dpi=RENDER_DPI).tobytes("png")


def describe(page: int, image: bytes) -> PageImage:
    """What the state keeps of a page image."""
    try:
        pix = pymupdf.Pixmap(image)
    except Exception as e:
        raise DocumentFileError(
            f"page {page} is not a readable image ({type(e).__name__})"
        ) from e
    return PageImage(
        page=page,
        sha256=hashlib.sha256(image).hexdigest(),
        size_bytes=len(image),
        width=pix.width,
        height=pix.height,
    )


def text_layer(path: str | Path, page: int) -> str | None:
    """
    A PDF page's own text (PyMuPDF), or None for an image or a page
    without text (a scan). Used only by validator 2.
    """
    path = Path(path)
    if _kind(path) == "image":
        return None
    with _open_pdf(path) as pdf:
        text = pdf[page - 1].get_text()
    return text if text.strip() else None
