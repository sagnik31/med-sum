"""
Small helpers for HTML built by code: escaping, numbers, and the page
wrapper with its stylesheet.

Every value that comes from a document is escaped where it is inserted
(`esc`), so a printed value that looks like HTML is shown as text. Fixed
wording lives in each view's TEXT table, so tests can tell the view's own
words from the record's.
"""

from html import escape

CSS = """
body { font-family: Arial, Helvetica, sans-serif; margin: 24px; line-height: 1.5; color: #222; }
h1 { font-size: 1.4em; margin: 0 0 8px; color: #013A63; }
h2 { color: #013A63; margin-top: 28px; border-bottom: 2px solid #013A63; padding-bottom: 4px; font-size: 1.15em; }
h3 { margin: 18px 0 6px; font-size: 1em; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; }
th, td { border: 1px solid #C4C4C4; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #E8F1FA; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px; margin: 8px 0; }
dt { color: #555; }
dd { margin: 0; }
.derived { color: #555; font-style: italic; }
.tag { font-size: 0.8em; color: #555; border: 1px solid #bbb; border-radius: 3px; padding: 0 4px; margin-left: 4px; font-style: normal; }
.review { background: #FFF7E0; }
.reasons { margin: 4px 0 0; padding-left: 18px; color: #7A4B00; }
.banner { background: #FFF7E0; border: 1px solid #E0B44C; padding: 8px 12px; margin: 12px 0; }
.muted { color: #666; }
.lines div { font-family: Consolas, monospace; white-space: pre-wrap; }
footer { margin-top: 32px; font-size: 0.85em; color: #666; }
"""


def esc(value) -> str:
    """A value as HTML text: escaped, quotes included."""
    return escape(str(value), quote=True)


def num(value: float | int) -> str:
    """A number as code prints it: no trailing .0, no exponent."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return str(value)
    text = format(value, ".10g")
    if "e" in text:
        text = f"{value:f}".rstrip("0").rstrip(".")
    return text


def page(title: str, body: str) -> str:
    """A whole HTML document around `body`; `title` is fixed text."""
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8">\n'
        f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )
