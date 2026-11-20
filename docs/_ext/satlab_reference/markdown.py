"""MyST Markdown building blocks for the generated reference pages.

Everything that comes from code or seed data goes through ``text``, ``prose`` or ``code`` here:
the pages are public, and a seed string or a docstring must never turn into markup or raw HTML.
"""

import re
from collections.abc import Iterable, Sequence

# Every ASCII punctuation character: CommonMark lets each be backslash-escaped to a literal.
_PUNCT = re.compile(r"([!-/:-@\[-`{-~])")
_URL = re.compile(r"https://[^\s<>\"'`]*[^\s<>\"'`.,;:)\]]")
# reST literals in docstrings and #: comments: ``x`` (and, here and there, `x`).
_LITERAL = re.compile(r"``(.+?)``|`([^`]+)`")


def _escape(s: str) -> str:
    return _PUNCT.sub(r"\\\1", s)


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def _inline(s: str) -> str:
    out, last = [], 0
    for m in _URL.finditer(s):
        out.append(_escape(s[last : m.start()]))
        out.append(f"<{m.group(0)}>")
        last = m.end()
    out.append(_escape(s[last:]))
    return "".join(out)


def text(value: object) -> str:
    """Plain text on one line: metacharacters escaped, ``https://`` URLs as autolinks."""
    return _inline(_one_line(value))


def prose(value: object) -> str:
    """Docstring-style text on one line: reST literals become code, the rest as ``text``."""
    s = _one_line(value)
    out, last = [], 0
    for m in _LITERAL.finditer(s):
        out.append(_inline(s[last : m.start()]))
        out.append(code(m.group(1) or m.group(2)))
        last = m.end()
    out.append(_inline(s[last:]))
    return "".join(out)


def paragraphs(value: str | None) -> str:
    """A docstring's paragraphs (blank-line separated), each through ``prose``."""
    if not value:
        return ""
    blocks = re.split(r"\n\s*\n", value.strip())
    return "\n\n".join(prose(b) for b in blocks if b.strip()) + "\n"


def code(value: object) -> str:
    """An inline code span holding ``value`` verbatim (on one line)."""
    s = _one_line(value)
    if not s:
        return ""
    longest = max((len(run) for run in re.findall(r"`+", s)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if s.startswith("`") or s.endswith("`") else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def heading(level: int, title: str, label: str | None = None) -> str:
    """A heading (``title`` is already Markdown) with an optional explicit target."""
    target = f"({label})=\n" if label else ""
    return f"{target}{'#' * level} {title}\n"


def list_table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """A ``list-table``: cells are Markdown already; empty cells show a dash."""
    lines = ["::::{list-table}", ":header-rows: 1", ""]
    for row in [header, *rows]:
        if len(row) != len(header):
            raise ValueError(f"row has {len(row)} cells, header {len(header)}")
        for i, cell in enumerate(row):
            lines.append(f"{'* -' if i == 0 else '  -'} {cell or '—'}")
    lines.append("::::")
    return "\n".join(lines) + "\n"


def bullets(items: Iterable[str]) -> str:
    """A bullet list of Markdown items."""
    return "".join(f"- {item}\n" for item in items)
