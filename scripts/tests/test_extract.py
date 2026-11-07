"""Page rendering and transcription (extract_report_slm), without a model."""

import os

import pymupdf
import pytest

import extract_report_slm as extract

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def make_pdf(path, pages: int) -> str:
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        doc.new_page().insert_text((72, 72), f"Fictional report, page {n}")
    doc.save(str(path))
    doc.close()
    return str(path)


def make_png(path) -> str:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    pix.save(str(path))
    return str(path)


@pytest.fixture
def transcriber(monkeypatch):
    """Replace the model call: record each page image and return its label."""
    seen = []

    def fake(image_bytes, system_prompt, label):
        seen.append({"image": image_bytes, "prompt": system_prompt, "label": label})
        return f"text of {label}"

    monkeypatch.setattr(extract, "run_vlm_on_image_bytes", fake)
    return seen


def test_each_pdf_page_is_rendered_to_png_and_joined_in_order(tmp_path, transcriber):
    pdf = make_pdf(tmp_path / "report.pdf", pages=3)

    markdown = extract.extract_markdown_from_file(pdf)

    assert [call["label"] for call in transcriber] == [
        "report.pdf - page 1/3",
        "report.pdf - page 2/3",
        "report.pdf - page 3/3",
    ]
    assert all(call["image"].startswith(PNG_SIGNATURE) for call in transcriber)
    assert markdown == "\n\n".join(f"text of {c['label']}" for c in transcriber)


def test_the_extraction_prompt_is_used(tmp_path, transcriber):
    pdf = make_pdf(tmp_path / "report.pdf", pages=1)

    extract.extract_markdown_from_file(pdf)

    with open(extract.EXTRACTION_PROMPT_FILE, encoding="utf-8") as f:
        assert transcriber[0]["prompt"] == f.read()


def test_heartbeat_runs_before_each_page(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(
        extract,
        "run_vlm_on_image_bytes",
        lambda image, prompt, label: events.append("page") or "text",
    )
    pdf = make_pdf(tmp_path / "report.pdf", pages=2)

    extract.extract_markdown_from_file(pdf, on_page=lambda: events.append("beat"))

    assert events == ["beat", "page", "beat", "page"]


def test_empty_pages_are_left_out(tmp_path, monkeypatch):
    replies = iter(["page one", "", "page three"])
    monkeypatch.setattr(
        extract, "run_vlm_on_image_bytes", lambda *args, **kwargs: next(replies)
    )
    pdf = make_pdf(tmp_path / "report.pdf", pages=3)

    assert extract.extract_markdown_from_file(pdf) == "page one\n\npage three"


def test_a_pdf_over_the_page_cap_is_refused_before_any_model_call(
    tmp_path, transcriber
):
    pdf = make_pdf(tmp_path / "long.pdf", pages=extract.MAX_PDF_PAGES + 1)

    with pytest.raises(ValueError, match=f"limit is {extract.MAX_PDF_PAGES}"):
        extract.extract_markdown_from_file(pdf)
    assert transcriber == []


def test_a_pdf_at_the_page_cap_is_accepted(tmp_path, transcriber):
    pdf = make_pdf(tmp_path / "long.pdf", pages=extract.MAX_PDF_PAGES)

    extract.extract_markdown_from_file(pdf)

    assert len(transcriber) == extract.MAX_PDF_PAGES


def test_the_pdf_is_closed_after_success(tmp_path, transcriber):
    # An open handle locks the file on Windows, so the backend could not
    # delete the upload (the bug fixed in aab34b0).
    pdf = make_pdf(tmp_path / "report.pdf", pages=2)

    extract.extract_markdown_from_file(pdf)

    os.remove(pdf)


def test_the_pdf_is_closed_after_a_failed_page(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(extract, "run_vlm_on_image_bytes", fail)
    pdf = make_pdf(tmp_path / "report.pdf", pages=2)

    with pytest.raises(RuntimeError, match="model down"):
        extract.extract_markdown_from_file(pdf)
    os.remove(pdf)


@pytest.mark.parametrize("name", ["scan.png", "scan.jpg", "scan.JPEG"])
def test_an_image_is_sent_as_is(tmp_path, transcriber, name):
    path = make_png(tmp_path / name)

    markdown = extract.extract_markdown_from_file(path)

    with open(path, "rb") as f:
        assert transcriber[0]["image"] == f.read()
    assert transcriber[0]["label"] == name
    assert markdown == f"text of {name}"


def test_unsupported_types_are_refused(tmp_path, transcriber):
    path = tmp_path / "notes.txt"
    path.write_text("not a scan")

    with pytest.raises(ValueError, match="Unsupported file type"):
        extract.extract_markdown_from_file(str(path))
    assert transcriber == []


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract.extract_markdown_from_file(str(tmp_path / "gone.pdf"))


def test_one_page_is_sent_to_the_model_with_the_image_and_end_marker(model_client):
    model_client.replies[extract.PRIMARY_MODEL] = [
        f"| Test | Value |\n{extract.END_MARKER}\nextra"
    ]

    text = extract.run_vlm_on_image_bytes(b"image-bytes", "PROMPT", label="page 1")

    assert text == "| Test | Value |"
    system, user = model_client.calls[0]["messages"]
    assert system == {"role": "system", "content": "PROMPT"}
    assert user["images"] == [b"image-bytes"]
    assert extract.END_MARKER in user["content"]
    assert (
        model_client.calls[0]["options"]["num_predict"] == extract.MAX_TOKENS_PER_PAGE
    )
