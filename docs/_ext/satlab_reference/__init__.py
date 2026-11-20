"""The Sphinx extension behind the SatLab docs (see how-to/write-docs).

- Generates the reference pages from code (``render``) into ``docs/reference/_generated/``.
- Rewrites links in Markdown included from outside ``docs/`` (``links``) and refuses to include
  anything private.
- Drops pydantic class signatures, which would publish evaluated defaults (machine paths).
- Checks the built HTML before it can be published (``guards``).
"""

from pathlib import Path

from pydantic import BaseModel
from sphinx.application import Sphinx
from sphinx.errors import ExtensionError
from sphinx.util import logging

from . import guards, links, render

logger = logging.getLogger(__name__)

# Repo files that are whole pages of the site, by docname: links to them stay on the site.
PUBLISHED = {"design-system.md": "project/design-system"}


def _generate(app: Sphinx) -> None:
    docs = Path(app.srcdir)
    if app.builder.format == "html":
        vendor = docs / "_static" / "vendor"
        if not (vendor / "VERSIONS.json").is_file():
            raise ExtensionError(
                "docs/_static/vendor is missing: run `pnpm -C frontend docs:vendor` "
                "(Mermaid and the fonts come from the frontend lockfile)"
            )
    out = docs / "reference" / "_generated"
    out.mkdir(parents=True, exist_ok=True)
    for name, page in render.PAGES.items():
        try:
            content = page()
        except render.ReferenceError as e:
            raise ExtensionError(f"reference/_generated/{name}: {e}") from e
        target = out / name
        # Only when changed, so sphinx-autobuild doesn't rebuild forever.
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")


def _on_include(app: Sphinx, relative_path: Path, parent: str, content: list[str]) -> None:
    docs = Path(app.srcdir).resolve()
    repo = docs.parent
    path = (docs / relative_path).resolve()
    rel = path.relative_to(repo).as_posix() if path.is_relative_to(repo) else None
    if rel is None or rel.startswith(links.PRIVATE):
        raise ExtensionError(f"{parent}: {relative_path} is private or outside the repo")
    if path.is_relative_to(docs):
        return

    def missing(target: str) -> None:
        logger.warning(f"{rel} (included in {parent}) links to a missing file: {target}")

    content[0] = links.rewrite_repo_links(
        content[0], path, repo, PUBLISHED, app.config.satlab_repo_blob, missing
    )


def _model_signature(
    app: Sphinx, what: str, name: str, obj: object, options: object, signature: str, ret: str
) -> tuple[str, None] | None:
    if what == "class" and isinstance(obj, type) and issubclass(obj, BaseModel):
        return "", None  # fields are documented as attributes; the signature has their values
    return None


def _check_output(app: Sphinx, exception: Exception | None) -> None:
    if exception is not None or app.builder.format != "html":
        return
    machine_paths = [str(Path(app.srcdir).resolve().parent)]
    home = str(Path.home())
    if home not in ("/", "/root"):
        machine_paths.append(home)
    problems: list[str] = []
    scripts: set[str] = set()
    for page in sorted(Path(app.outdir).rglob("*.html")):
        html = page.read_text(encoding="utf-8")
        scripts |= guards.inline_scripts(html)
        rel = page.relative_to(app.outdir)
        problems += [f"{rel}: {p}" for p in guards.check_html(html, machine_paths)]
    if problems:
        raise ExtensionError("unfit to publish:\n  " + "\n  ".join(problems[:20]))
    logger.info(f"satlab_reference: {len(scripts)} distinct inline scripts (hashed into the CSP)")


def setup(app: Sphinx) -> dict[str, object]:
    """Register the config value and the event handlers."""
    app.add_config_value("satlab_repo_blob", "", "env")
    app.connect("builder-inited", _generate)
    app.connect("include-read", _on_include)
    app.connect("autodoc-process-signature", _model_signature)
    app.connect("build-finished", _check_output)
    return {"version": "1", "parallel_read_safe": True, "parallel_write_safe": True}
