"""Joining the two halves of a page read again after its reading was capped."""

import pymupdf
import pytest

from dataset.build import DATASET_DIR
from pipeline import pages
from pipeline.reread import EDGE_LINES, HALVES, MIN_OVERLAP, join_halves

PAGE = [f"| Test {i} | {i}.0 | mg/dL |" for i in range(1, 21)]


def test_halves_join_where_they_overlap():
    lines, overlap = join_halves(PAGE[:12], PAGE[8:])
    assert lines == PAGE and overlap == 4


def test_lines_cut_through_by_an_edge_are_dropped_at_the_join():
    top = [*PAGE[:12], "| Test 13 | 1"]  # the top half's edge cuts row 13
    bottom = ["st 8 | 8.0 |", *PAGE[8:]]  # the bottom's cuts row 8
    lines, overlap = join_halves(top, bottom)
    assert lines == PAGE and overlap == 4


def test_blank_lines_and_spacing_at_the_ends_do_not_break_the_overlap():
    top = [*PAGE[:10], "", "  " + PAGE[10] + " "]
    bottom = [PAGE[9], "", PAGE[10], *PAGE[11:]]
    lines, _ = join_halves(top, bottom)
    assert [x.strip() for x in lines if x.strip()] == PAGE


def test_one_shared_line_is_not_enough():
    assert MIN_OVERLAP == 2
    assert join_halves(PAGE[:10], PAGE[9:]) is None


def test_no_shared_lines_is_not_joined():
    assert join_halves(PAGE[:10], ["something else", "entirely"]) is None


def test_an_overlap_far_from_the_edges_is_not_used():
    # The shared run sits more than EDGE_LINES lines from the top half's end.
    top = [*PAGE[:10], *(f"noise {i}" for i in range(EDGE_LINES + 1))]
    assert join_halves(top, PAGE[8:]) is None


def test_an_overlap_found_in_two_places_is_not_joined():
    # Two identical rows: the bottom half could follow either copy.
    top = ["| Protein | Nil |", "| Glucose | Nil |", "x", "| Protein | Nil |",
           "| Glucose | Nil |"]  # fmt: skip
    assert join_halves(top, ["| Protein | Nil |", "| Glucose | Nil |", "end"]) is None


def test_the_longest_overlap_wins():
    # A run of two that also appears inside the true four-line overlap.
    lines, overlap = join_halves(PAGE[:12], PAGE[8:])
    assert overlap == 4 and lines.count(PAGE[10]) == 1


def test_the_halves_overlap_and_cover_the_page():
    (t0, t1), (b0, b1) = HALVES
    assert t0 == 0.0 and b1 == 1.0 and b0 < t1


@pytest.mark.parametrize("band", HALVES)
def test_a_band_is_cut_from_the_page_image_at_full_width(band):
    image = pages.page_png(DATASET_DIR / "documents" / "d25_lab_report.pdf", 1)
    full = pymupdf.Pixmap(image)
    cut = pymupdf.Pixmap(pages.crop_band(image, *band))
    assert cut.width == full.width
    assert cut.height == round(full.height * band[1]) - round(full.height * band[0])
    # The band's first row of pixels is the page's row at the band's top.
    top = round(full.height * band[0])
    assert cut.pixel(cut.width // 2, 0) == full.pixel(full.width // 2, top)
