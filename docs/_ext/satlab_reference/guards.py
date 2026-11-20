"""Checks on the built HTML. The site is public and served under a strict CSP (AGENTS.md §12.7):
no script from another origin, no inline event handlers or ``javascript:`` URLs, and nothing
from the machine that built it (its paths, or a URL carrying credentials)."""

import re

_INLINE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", re.IGNORECASE)
_TAG = re.compile(r"<[a-zA-Z][^>]*>")
_OFF_ORIGIN_SCRIPT = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["']?\s*(?:[a-z][a-z0-9+.-]*:)?//""", re.IGNORECASE
)
_HANDLER = re.compile(r"""\son[a-z]+\s*=""", re.IGNORECASE)
_JS_URL = re.compile(r"""\b(?:href|src|action)\s*=\s*["']?\s*javascript:""", re.IGNORECASE)
_CREDENTIALS = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@\"'<>]+:([^/\s@\"'<>]+)@", re.IGNORECASE)
# Documented placeholders, as they appear in the HTML: <password>, ***, ...
_PLACEHOLDER = re.compile(r"^(?:&lt;[^&]*&gt;|\*+|\.\.\.|…|password)$", re.IGNORECASE)


def inline_scripts(html: str) -> set[str]:
    """The bodies of the page's inline scripts (each needs a CSP hash)."""
    return set(_INLINE.findall(html))


def check_html(html: str, machine_paths: list[str]) -> list[str]:
    """Problems with one built page; empty when it's fit to publish."""
    problems = []
    if _OFF_ORIGIN_SCRIPT.search(html):
        problems.append("a script loaded from another origin")
    markup = _INLINE.sub("<script>", html)
    for tag in _TAG.findall(markup):
        if _HANDLER.search(tag):
            problems.append(f"an inline event handler: {tag[:80]}")
        if _JS_URL.search(tag):
            problems.append(f"a javascript: URL: {tag[:80]}")
    for m in _CREDENTIALS.finditer(html):
        if not _PLACEHOLDER.match(m.group(1)):
            problems.append("a URL with credentials in it")
    for path in machine_paths:
        if re.search(rf"(?<![\w./-]){re.escape(path)}(?![\w-])", html):
            problems.append(f"the build machine's path {path!r}")
    return problems
