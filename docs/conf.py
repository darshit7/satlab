"""Sphinx configuration for the SatLab documentation site (docs-as-code; see how-to/write-docs).

Build it from ``backend/`` (the docs need the backend's dependencies, autodoc imports ``app``)::

    uv run --group docs sphinx-build -M dirhtml ../docs ../docs/_build -W --keep-going

Published with the web image at https://satlab.darshitp.dev/satlab/docs/.
"""

import os
import sys
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent
REPO_DIR = DOCS_DIR.parent
sys.path[:0] = [str(REPO_DIR / "backend"), str(DOCS_DIR / "_ext")]

# The site is public and built from code defaults only: never from the developer's shell or .env
# (database URLs, tokens), so a local build matches CI and the image. Done before anything imports
# app.config, so the first get_settings() (app.main calls it on import) sees neither.
for _name in [n for n in os.environ if n.startswith("SATLAB_")]:
    del os.environ[_name]
from app.config import Settings  # noqa: E402

Settings.model_config["env_file"] = None

# Where the site is served. Absolute, so Mermaid's inline init script is the same on every page
# (one CSP hash); DOCS_BASE_PATH=/ for a preview served at the root (sphinx-autobuild on its own).
BASE_PATH = os.environ.get("DOCS_BASE_PATH", "/satlab/docs/")
GITHUB_REPO = "https://github.com/darshit7/satlab"

project = "SatLab"
author = "Darshit Patoliya"
copyright = "2026, Darshit Patoliya"
html_title = "SatLab docs"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinxcontrib.mermaid",
    "notfound.extension",
    "satlab_reference",
]

source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
root_doc = "index"
exclude_patterns = [
    "_build",
    "_ext",
    "_intersphinx",
    "_templates",
    "reference/_generated",  # included into the reference pages, not pages of their own
]
templates_path = ["_templates"]
# Docstrings use reST: ``literal`` and, here and there, `literal` too.
default_role = "literal"

# ---------- MyST (the Markdown pages) ----------
# No dollarmath: it would make Sphinx load MathJax from a CDN. Formulas are code blocks.
myst_enable_extensions = ["colon_fence", "deflist", "tasklist"]
myst_heading_anchors = 3

# ---------- API reference (autodoc + autosummary) ----------
autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    # Evaluated values would publish machine paths (Settings defaults, *_FILE constants).
    "no-value": True,
    # Framework plumbing, not ours: pydantic's model_* attributes, SQLAlchemy's DeclarativeBase.
    "exclude-members": "model_config,model_fields,model_computed_fields,metadata,registry",
}
autodoc_member_order = "bysource"
# A class without its own docstring would show BaseModel's (or SQLAlchemy's) instead.
autodoc_inherit_docstrings = False
autodoc_preserve_defaults = True  # defaults as written in the source, not evaluated
autodoc_typehints_format = "short"
python_use_unqualified_type_names = True

# Offline and deterministic: the inventories are vendored (docs/_intersphinx/README.md).
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", "_intersphinx/python.inv"),
    "numpy": ("https://numpy.org/doc/stable", "_intersphinx/numpy.inv"),
    "sqlalchemy": ("https://docs.sqlalchemy.org/en/21", "_intersphinx/sqlalchemy.inv"),
    "pydantic": ("https://pydantic.dev/docs/validation/latest", "_intersphinx/pydantic.inv"),
    "fastapi": ("https://fastapi.tiangolo.com", "_intersphinx/fastapi.inv"),
}
intersphinx_timeout = 5

# ---------- Diagrams ----------
# Mermaid is vendored from the frontend lockfile (pnpm -C frontend docs:vendor): no CDN.
mermaid_use_local = f"{BASE_PATH}_static/vendor/mermaid/mermaid.esm.min.mjs"
mermaid_d3_zoom = False

# ---------- HTML (Furo, in the app's Warm Notebook tokens) ----------
html_theme = "furo"
html_baseurl = f"https://satlab.darshitp.dev{BASE_PATH}"
html_static_path = ["_static"]
html_css_files = ["vendor/fonts.css", "satlab.css"]
html_js_files = ["satlab-diagrams.js"]
html_favicon = "_static/vendor/favicon.svg"
html_copy_source = False
html_show_sourcelink = False
html_last_updated_fmt = None
pygments_style = "solarized-light"
pygments_dark_style = "github-dark"

_light = {
    "color-brand-primary": "#047857",  # primary-700
    "color-brand-content": "#047857",
    "color-brand-visited": "#065f46",  # primary-800
    "color-foreground-primary": "#111827",  # gray-900
    "color-foreground-secondary": "#4b5563",  # gray-600
    "color-foreground-muted": "#6b7280",  # gray-500
    "color-foreground-border": "#d1d5db",  # gray-300
    "color-background-primary": "#ffffff",
    "color-background-secondary": "#f9fafb",  # gray-50
    "color-background-hover": "#f3f4f6",  # gray-100
    "color-background-border": "#e5e7eb",  # gray-200
    "color-background-item": "#d1d5db",
    "color-code-background": "#fdfaf6",  # design-system §3: solarized-light ground
    "color-code-foreground": "#36313d",  # code-block text
    "color-inline-code-background": "#f3f4f6",
    "color-highlighted-background": "#fbf0ea",
    "color-admonition-title--note": "#047857",
    "color-admonition-title-background--note": "#d1fae5",
    "color-admonition-title--tip": "#047857",
    "color-admonition-title-background--tip": "#d1fae5",
    "color-admonition-title--warning": "#b45309",  # amber-700
    "color-admonition-title-background--warning": "#fdfaf6",
    "color-admonition-title--danger": "#b91c1c",  # red-700
    "color-admonition-title-background--danger": "#fdfaf6",
    "font-stack": "'Nunito Variable', system-ui, sans-serif",
    "font-stack--monospace": "'JetBrains Mono Variable', ui-monospace, monospace",
    "font-stack--headings": "'Nunito Variable', system-ui, sans-serif",
}
_dark = {
    "color-brand-primary": "#34d399",  # primary-400
    "color-brand-content": "#34d399",
    "color-brand-visited": "#6ee7b7",  # primary-300
    "color-foreground-primary": "#f3f4f6",  # gray-100
    "color-foreground-secondary": "#9ca3af",  # gray-400
    "color-foreground-muted": "#9ca3af",
    "color-foreground-border": "#4b5563",  # gray-600
    "color-background-primary": "#1f1f1f",  # dark
    "color-background-secondary": "#2a2a2a",  # white/5 over dark
    "color-background-hover": "#353535",  # white/10 over dark
    "color-background-border": "#374151",  # gray-700
    "color-background-item": "#4b5563",
    "color-code-background": "#22272e",  # github-dark-dimmed ground
    "color-code-foreground": "#f3f4f6",
    "color-inline-code-background": "#2a2a2a",
    "color-highlighted-background": "#36313d",
    "color-admonition-title--note": "#34d399",
    "color-admonition-title-background--note": "#2a2a2a",
    "color-admonition-title--tip": "#34d399",
    "color-admonition-title-background--tip": "#2a2a2a",
    "color-admonition-title--warning": "#fbbf24",  # amber-400
    "color-admonition-title-background--warning": "#2a2a2a",
    "color-admonition-title--danger": "#f87171",  # red-400
    "color-admonition-title-background--danger": "#2a2a2a",
}
html_context = {"satlab_app_url": BASE_PATH.removesuffix("docs/")}
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/app-link.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
    ]
}
html_theme_options = {
    "light_css_variables": _light,
    "dark_css_variables": _dark,
    "source_repository": f"{GITHUB_REPO}/",
    "source_branch": "main",
    "source_directory": "docs/",
    "top_of_page_buttons": ["edit"],
    "navigation_with_keys": True,
}

# ---------- 404 (sphinx-notfound-page) ----------
notfound_urls_prefix = BASE_PATH

# ---------- satlab_reference (docs/_ext) ----------
satlab_repo_blob = f"{GITHUB_REPO}/blob/main/"
