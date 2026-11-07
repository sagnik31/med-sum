"""
Print synthetic documents with PyMuPDF (already a dependency): HTML laid
out into PDF pages, then rasterised for prescriptions (PNG, JPEG) and for
image-only PDFs, which have no text layer, like a scan.

Output is byte-for-byte the same on every run with the pinned PyMuPDF.
"""

import io

import pymupdf

from dataset.content import Doc
from dataset.layout import page_html

DPI = 150
MARGIN = 36

CSS = """
body { font-family: sans-serif; font-size: 9.5pt; color: #111; }
h1 { font-size: 15pt; margin: 0 0 2pt 0; }
h2 { font-size: 10.5pt; margin: 10pt 0 4pt 0; }
p { margin: 0 0 3pt 0; }
p.footer { margin-top: 14pt; font-size: 7.5pt; color: #555; }
table { border-collapse: collapse; width: 100%; margin-bottom: 4pt; }
th, td { border: 1px solid #555; padding: 2pt 4pt; text-align: left; }
th { background-color: #e8eef4; }
.nw { white-space: nowrap; }
"""


def pdf_bytes(doc: Doc) -> bytes:
    """The document as a PDF with a text layer, one HTML story per page."""
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    mediabox = pymupdf.paper_rect(doc.paper)
    where = mediabox + (MARGIN, MARGIN, -MARGIN, -MARGIN)
    for n, blocks in enumerate(doc.page_blocks(), start=1):
        story = pymupdf.Story(html=page_html(blocks), user_css=CSS)
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
        if more:
            raise ValueError(f"{doc.id} page {n} does not fit on one {doc.paper} page")
    writer.close()
    return _normalised(pymupdf.open("pdf", buffer.getvalue()))


def _normalised(pdf: pymupdf.Document) -> bytes:
    """No dates or random IDs, so every build is identical."""
    pdf.set_metadata({})
    data = pdf.tobytes(garbage=3, deflate=True, no_new_id=True)
    pdf.close()
    return data


def page_images(pdf: bytes, image_format: str = "png") -> list[bytes]:
    with pymupdf.open("pdf", pdf) as source:
        return [
            page.get_pixmap(dpi=DPI).tobytes(image_format, jpg_quality=90)
            for page in source
        ]


def scanned_pdf(pdf: bytes) -> bytes:
    """Each page replaced by a picture of itself: no text layer."""
    out = pymupdf.open()
    with pymupdf.open("pdf", pdf) as source:
        for page, image in zip(source, page_images(pdf)):
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, stream=image)
    return _normalised(out)


def render(doc: Doc) -> bytes:
    """The file that gets uploaded for this document."""
    pdf = pdf_bytes(doc)
    if doc.file_format == "pdf":
        return pdf
    if doc.file_format == "scan_pdf":
        return scanned_pdf(pdf)
    if len(doc.pages) != 1:
        raise ValueError(f"{doc.id}: an image file holds exactly one page")
    image_format = {"png": "png", "jpg": "jpeg"}[doc.file_format]
    return page_images(pdf, image_format)[0]
