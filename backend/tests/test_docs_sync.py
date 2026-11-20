"""The docs stay tied to the code they explain (docs/sync-map.toml, docs/how-to/write-docs.md).

Not a diff check: every source file must belong to at least one docs page, so a new module, view
or deploy file fails here until someone decides which page explains it. The reference pages are
generated from the code, and the strict docs build (CI's ``docs`` job) catches broken links.
"""

import re
import tomllib
from pathlib import Path

from app.config import Settings

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
_IGNORED_PARTS = {"__pycache__", "node_modules", "dist", ".venv"}


def _entries() -> list[dict[str, list[str]]]:
    return tomllib.loads((DOCS / "sync-map.toml").read_text(encoding="utf-8"))["entry"]


def _files_under(directory: Path) -> set[Path]:
    return {
        p
        for p in directory.rglob("*")
        if p.is_file() and not _IGNORED_PARTS & set(p.relative_to(REPO).parts)
    }


def _matches(pattern: str) -> set[Path]:
    if pattern.endswith("/"):
        return _files_under(REPO / pattern)
    return {p for p in REPO.glob(pattern) if p.is_file()}


def _source_files() -> set[Path]:
    """Every file whose behaviour some docs page must explain."""
    backend = REPO / "backend"
    files = {p for p in _files_under(backend / "app") if p.suffix == ".py"}
    files = {p for p in files if p.name != "__init__.py"}
    files |= set((backend / "app" / "data" / "seed").glob("*.json"))
    files |= set((backend / "migrations" / "versions").glob("*.py"))
    files |= {
        p
        for p in _files_under(REPO / "frontend" / "src")
        if not re.search(r"\.test\.tsx?$", p.name) and p.name != "vite-env.d.ts"
    }
    for directory in ("frontend/nginx", "frontend/scripts", ".github/workflows", "docs/_ext"):
        files |= _files_under(REPO / directory)
    files |= {
        REPO / f
        for f in (
            "backend/Dockerfile",
            "frontend/Dockerfile",
            "frontend/Dockerfile.dockerignore",
            "frontend/vite.config.ts",
            "scripts/smoke.sh",
            "docs/conf.py",
        )
    }
    return files


def test_entries_are_well_formed() -> None:
    entries = _entries()
    assert entries
    for entry in entries:
        assert set(entry) == {"paths", "pages"}, entry
        assert entry["paths"] and all(isinstance(p, str) for p in entry["paths"]), entry
        assert entry["pages"] and all(isinstance(p, str) for p in entry["pages"]), entry


def test_every_page_exists() -> None:
    missing = [
        page
        for entry in _entries()
        for page in entry["pages"]
        if not any((DOCS / f"{page}{ext}").is_file() for ext in (".md", ".rst"))
    ]
    assert not missing, f"sync-map.toml names pages that don't exist: {missing}"


def test_every_path_matches_something() -> None:
    stale = [p for entry in _entries() for p in entry["paths"] if not _matches(p)]
    assert not stale, f"sync-map.toml paths that match no file (moved or deleted?): {stale}"


def test_all_source_files_are_mapped() -> None:
    covered: set[Path] = set()
    for entry in _entries():
        for pattern in entry["paths"]:
            covered |= _matches(pattern)
    unmapped = sorted(str(p.relative_to(REPO)) for p in _source_files() - covered)
    assert not unmapped, (
        "these files aren't explained by any docs page; add them to docs/sync-map.toml "
        f"(and write or update the page): {unmapped}"
    )


def test_env_example_lists_every_setting() -> None:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    listed = {name.lower() for name in re.findall(r"\bSATLAB_([A-Z0-9_]+)", text)}
    missing = [name for name in Settings.model_fields if name not in listed]
    assert not missing, f".env.example is missing these settings: {missing}"
