"""The docs site's generated reference pages and guards (docs/_ext/satlab_reference).

Runs where the ``docs`` dependency group is installed (CI's docs job); skipped elsewhere.
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("sphinx")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "docs" / "_ext"))

from satlab_reference import guards, links, markdown, render  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models import ws  # noqa: E402


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    return {name: page() for name, page in render.PAGES.items()}


def test_no_machine_paths_anywhere(pages: dict[str, str]) -> None:
    for name, text in pages.items():
        assert str(REPO) not in text, name
        assert str(Path.home()) not in text, name


def test_settings_lists_every_setting_with_its_source_default(pages: dict[str, str]) -> None:
    text = pages["settings.md"]
    for name in Settings.model_fields:
        assert f"`SATLAB_{name.upper()}`" in text
    # shown as written, never evaluated (an evaluated default is a path on this machine)
    assert "`f\"sqlite:///{VAR_DIR / 'satlab.db'}\"`" in text
    assert "**secret**" in text  # SecretStr fields are flagged


def test_websocket_lists_every_message_and_close_code(pages: dict[str, str]) -> None:
    text = pages["websocket.md"]
    for union in (ws.ServerMessage, ws.ClientCommand):
        for cls in render._union_members(union):
            assert f"(ws-{cls.__name__.lower()})=" in text
            assert f"`{cls.model_fields['type'].default}`" in text
    assert "(ws-auth)=" in text
    for code in ("1008", "4401", "4404", "4503"):
        assert f"`{code}`" in text
    assert "(ws-satellitestate)=" in text  # nested models get their own section


def test_database_has_every_table_and_relationship(pages: dict[str, str]) -> None:
    text = pages["database.md"]
    assert "erDiagram" in text
    for table in Base.metadata.sorted_tables:
        assert f"(db-{table.name.replace('_', '-')})=" in text
        for fk in table.foreign_keys:
            edge = f' {table.name} : "{fk.parent.name}"'
            assert any(edge in line and fk.column.table.name in line for line in text.splitlines())
    assert "`TIMESTAMP WITH TIME ZONE`" in text  # both dialects' types are shown


def test_rest_has_every_path(pages: dict[str, str]) -> None:
    from app.main import app

    text = pages["rest.md"]
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            assert f"`{method.upper()} {path}`" in text


def test_seed_data_cites_every_link_profile_source(pages: dict[str, str]) -> None:
    from app.data.seed import link_profile_specs

    text = pages["seed-data.md"]
    for profile in link_profile_specs().profiles:
        for source in profile.sources:
            assert f"<{source.url}>" in text


def test_text_cannot_become_markup() -> None:
    hostile = "<script>alert(1)</script> [x](javascript:alert(1)) {role}`x` **b**"
    out = markdown.text(hostile)
    assert "<script>" not in out
    assert "](javascript:" not in out
    assert "{role}`" not in out
    assert markdown.prose("the ``auth`` message") == "the `auth` message"
    assert markdown.code("a`b") == "``a`b``"
    assert markdown.text("see https://example.org/a_b.") == "see <https://example.org/a_b>\\."


def test_links_in_included_files() -> None:
    repo = REPO
    source = repo / "AGENTS.md"
    seen: list[str] = []

    def rewrite(md: str) -> str:
        blob = "https://github.com/o/r/blob/main/"
        return links.rewrite_repo_links(md, source, repo, links_published, blob, seen.append)

    links_published = {"design-system.md": "project/design-system"}
    assert rewrite("[ds](design-system.md)") == "[ds](/project/design-system.md)"
    assert rewrite("[w](docs/how-to/write-docs.md)") == "[w](/how-to/write-docs.md)"
    assert rewrite("[n](deploy/neon.md)") == "n"  # private: text only
    assert rewrite("[d](notes/x.md)") == "d"
    assert rewrite("[ws](backend/app/models/ws.py#L1)") == (
        "[ws](https://github.com/o/r/blob/main/backend/app/models/ws.py#L1)"
    )
    assert rewrite("[dir](frontend/src/)") == "[dir](https://github.com/o/r/tree/main/frontend/src)"
    untouched = "[web](https://example.org) [a](#anchor)"
    assert rewrite(untouched) == untouched
    assert not seen
    rewrite("[gone](no/such/file.py)")
    assert seen == ["no/such/file.py"]


def test_output_guard() -> None:
    repo = str(REPO)
    furo_theme = (
        '<script>document.body.dataset.theme = localStorage.getItem("theme") || "auto";</script>'
    )
    own = '<script src="_static/x.js"></script>'
    assert guards.check_html(f"<html>{furo_theme}{own}", [repo]) == []
    assert guards.check_html('<script src="https://cdn.example.org/m.js"></script>', [repo])
    assert guards.check_html('<script src="//cdn.example.org/m.js"></script>', [repo])
    assert guards.check_html('<button onclick="x()">', [repo])
    assert guards.check_html('<a href="javascript:alert(1)">', [repo])
    assert guards.check_html(f"<p>{repo}/backend/app</p>", [repo])
    assert guards.check_html("<p>postgresql://app:hunter2@host/db</p>", [repo])
    assert not guards.check_html("<p>postgresql://app:&lt;password&gt;@host/db</p>", [repo])
    assert guards.inline_scripts(furo_theme)
