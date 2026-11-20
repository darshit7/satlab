"""Links in Markdown included from outside ``docs/`` (AGENTS.md, README.md, design-system.md).

Those files link to repo paths relative to themselves, which aren't pages of this site. A link
to a published page becomes a link to that page, a link into a private folder is dropped (its
text stays), and anything else points at the file on GitHub (the repo is public).
"""

import re
from collections.abc import Callable, Mapping
from pathlib import Path

# Never published: the design doc and the deploy runbooks (gitignored, local only).
PRIVATE = ("notes/", "deploy/")

_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\s]+)\)")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", re.IGNORECASE)


def rewrite_repo_links(
    markdown: str,
    source: Path,
    repo_dir: Path,
    published: Mapping[str, str],
    repo_blob: str,
    missing: Callable[[str], None],
) -> str:
    """Rewrite the relative links of ``markdown``, a file at ``source`` inside ``repo_dir``.

    Links to pages under ``docs/`` stay on the site. ``published`` maps the repo-relative paths of
    other files that are pages of the site to their docnames; ``repo_blob`` is the GitHub
    ``…/blob/<branch>/`` URL; ``missing`` is told about links to files that don't exist.
    """

    def replace(m: re.Match[str]) -> str:
        label, target = m.group(1), m.group(2)
        if _EXTERNAL.match(target):
            return m.group(0)
        path, _, anchor = target.partition("#")
        resolved = (source.parent / path).resolve()
        if not resolved.is_relative_to(repo_dir):
            missing(target)
            return label
        rel = resolved.relative_to(repo_dir).as_posix()
        if rel.startswith(PRIVATE) or f"{rel}/".startswith(PRIVATE):
            return label
        if rel in published:
            return f"[{label}](/{published[rel]}.md)"
        if rel.startswith("docs/") and resolved.suffix in (".md", ".rst"):
            return f"[{label}](/{rel.removeprefix('docs/')})"  # a page of this site
        if not resolved.exists():
            missing(rel)
        base = repo_blob.replace("/blob/", "/tree/") if resolved.is_dir() else repo_blob
        return f"[{label}]({base}{rel}{'#' + anchor if anchor else ''})"

    return _LINK.sub(replace, markdown)
