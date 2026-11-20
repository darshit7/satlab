# Write the docs

These docs are **docs-as-code**. They live in `docs/`, change in the same commit as the code they
describe, are built by CI on every push, and ship inside the web image at `/satlab/docs/`. This
page is how they work and how to keep them true.

## The rule

> **Docs change with the code** (AGENTS.md, golden rule 11). When a change touches a file, the
> pages that [the sync map](#the-sync-map) lists for it are reviewed and updated in the same
> change. Reference pages are generated, so they update themselves. CI builds the site strictly and
> fails on any warning.

## How the site is organised

The structure follows [Diátaxis](https://diataxis.fr/): each page has one job.

| Section | Job | Written by |
| ------- | --- | ---------- |
| `getting-started/` | learn by doing: run it, tour it | hand |
| `architecture/`, `simulation/` | explain how and **why** | hand, with diagrams |
| `how-to/` | step-by-step recipes for a task | hand |
| `reference/` | facts to look up | **generated from code**, with a short hand-written intro |
| `project/` | decisions, roadmap, design system, contributing | included from `AGENTS.md` and `design-system.md` |

### Explanation pages use one template

1. **What it is**: the behaviour, in user terms.
2. **Why it works this way**: the constraints and decisions (link the decision log).
3. **How it flows**: a Mermaid diagram and a numbered walkthrough.
4. **Where in the code**: cross-references into the API reference and the tests.
5. **Limits**: what is modelled, approximate or out of scope (the honesty rules).

## What is generated, and from where

| Page | Source | Generator |
| ---- | ------ | --------- |
| [Python API](../reference/api/index.md) | every module's docstrings and `#:` comments | autodoc + autosummary (recursive) |
| [REST API](../reference/rest.md) | the FastAPI app's OpenAPI document and the API models | `satlab_reference.render.rest_page` |
| [WebSocket](../reference/websocket.md) | the `ServerMessage` / `ClientCommand` unions, `Auth`, the `WS_CLOSE_*` constants | `websocket_page` |
| [Settings](../reference/settings.md) | `Settings` fields, read from source (defaults as written, never evaluated) | `settings_page` |
| [Database](../reference/database.md) | the ORM metadata, both dialects' types, the Alembic revisions | `database_page` |
| [Seed data](../reference/seed-data.md) | the seed files, through the app's own validated loaders | `seed_page` |

The extension `docs/_ext/satlab_reference/` writes those pages into
`docs/reference/_generated/` (gitignored) when the build starts. It writes a file only when its
content changed, so `sphinx-autobuild` doesn't loop. The hand-written page around each one includes
it. Every value is escaped, so a seed string can't become markup.

### Docstrings and `#:` comments are the API reference

- **Every public module, class, function and method has a docstring.** `ruff` enforces it (`D1`);
  tests and migrations are exempt. Write a PEP 257 summary line, then prose: units in names,
  ``reST literals`` for code. Don't use `Args:` / `Returns:` sections.
- **A field or constant is documented with a `#:` comment**, on the line itself or on the lines
  directly above it:

  ```python
  horizon_mask_deg: float = Field(default=5.0, ge=0.0, le=45.0)  #: elevation of the mask
  #: Rows that are older than this are refetched.
  catalog_max_age_hours: float = 12.0
  ```

  autodoc, the WebSocket and REST tables, and the settings page all read these. A plain `#`
  comment is for group headings and implementation notes, which the docs don't show.
- A method that implements a framework hook (e.g. a SQLAlchemy `TypeDecorator`) gets
  `@typing.override` instead of a boilerplate docstring.

## The sync map

`docs/sync-map.toml` says which pages explain which code:

```toml
[[entry]]
paths = ["backend/app/sim/linkbudget.py"]
pages = ["simulation/link-budget"]
```

`backend/tests/test_docs_sync.py` (part of the normal backend tests) fails when:

- a source file isn't covered by any entry: every backend module, seed file, migration, frontend
  source file, Dockerfile, nginx config, workflow and the docs tooling. A new module therefore
  forces a decision about which page explains it;
- an entry's path matches nothing (a file moved or was deleted);
- a page doesn't exist;
- a setting is missing from `.env.example`.

When you change a file, look up its entries and update those pages in the same commit.

## Single-sourcing

Text that already lives in `AGENTS.md`, `README.md` or `design-system.md` is **included**, not
copied. The source marks the section with HTML comments, which are invisible on GitHub:

```markdown
<!-- docs:glossary:start -->
…
<!-- docs:glossary:end -->
```

The page includes it with MyST:

````markdown
```{include} ../../AGENTS.md
:start-after: <!-- docs:glossary:start -->
:end-before: <!-- docs:glossary:end -->
```
````

Links inside included files are rewritten. A link to a published page stays on the site; any other
repo file becomes a GitHub link; a link into a private folder is dropped, keeping its text. Including
anything from `notes/` or `deploy/` fails the build: those stay private.

## Diagrams

Use Mermaid fences (`` ```{mermaid} ``): flowcharts, sequence diagrams, state diagrams and ER
diagrams.

- Mermaid runs in the browser from a copy vendored from the frontend lockfile, not a CDN. Its inline
  init script is the same on every page, so one CSP hash covers it.
- Don't use the `:zoom:` option: it makes the init script differ per page.
- Write formulas as code blocks. Math markup would make Sphinx load MathJax from a CDN.
- **Preview every diagram you change.** Mermaid parses in the browser, so the strict build can't
  catch a syntax error; the page shows an error box instead. A common trap: in a sequence
  diagram, `;` ends a statement, so never put one in a message.
- Prefer `flowchart TB` to `LR`. The column is about 700 px wide, so a wide left-to-right chart is
  scaled down until its text is unreadable.

## Build and preview

```bash
pnpm -C frontend install && pnpm -C frontend docs:vendor   # Mermaid + fonts into docs/_static/vendor
cd backend
uv run --group docs sphinx-build -M dirhtml ../docs ../docs/_build -W --keep-going
```

For a live preview while writing:

```bash
cd backend
WATCHFILES_FORCE_POLLING=true uv run --group docs sphinx-autobuild ../docs ../docs/_build/dirhtml \
  -b dirhtml --port 8001 --watch app \
  --ignore "*/_generated/*" --ignore "*/_autosummary/*"
```

Then open `http://localhost:5173/satlab/docs/`: `pnpm dev` proxies that path to port 8001, so the
app's Docs link and the absolute asset paths work. (To browse port 8001 directly instead, add
`DOCS_BASE_PATH=/`; the site is otherwise built for `/satlab/docs/`.) Polling is needed on `/mnt/*`
drives under WSL, where file events don't arrive.

The build runs these checks; any warning fails it:

- **Strict Sphinx** (`-W`): broken cross-references and links, pages missing from a toctree, bad
  reST in a docstring, duplicate labels.
- **The generator checks the code**: the settings it parsed must match `Settings`'s fields, the
  close-code constants must exist, and the migrations must form one linear history.
- **The output guard** (`satlab_reference.guards`) refuses a built page with:
  - a script from another origin;
  - an inline event handler or a `javascript:` URL;
  - the build machine's paths, or a URL with credentials in it.
- **The env is scrubbed.** `docs/conf.py` removes `SATLAB_*` variables and turns off `.env`
  loading before anything imports the app, so a local build publishes exactly what CI would.

## Serving: the CSP

The docs are served by the web container's nginx at `/satlab/docs/` with their own
Content-Security-Policy (`frontend/nginx/docs-security-headers.conf`). Its `script-src` lists the
SHA-256 of every distinct inline script in the built site, computed during the image build by
`frontend/scripts/csp-script-hashes.mjs`. Today there are two: Furo's theme line and the Mermaid
init. No external host is allowed at all.

## Housekeeping

- **Intersphinx inventories** (`docs/_intersphinx/`) are vendored so the build is offline. Refresh
  them deliberately (the README there has the commands).
- **Theme handoff.** `docs/_static/satlab-theme.js` copies the app's theme choice to Furo's, so the
  docs open in the same theme as the simulator.
- **Styling** uses the design system's token values only (`docs/conf.py`, `_static/satlab.css`).
- **Diagram sizing.** `docs/_static/satlab-diagrams.js` keeps each Mermaid diagram at its natural
  size and shrinks only one wider than the column; the plugin alone stretches every diagram to
  the column, small ones included. Full screen (the diagram's corner button) still stretches.

## Checklist for a change

- [ ] Docstrings and `#:` comments on new public code, fields, settings and constants.
- [ ] The pages the sync map lists for the files you touched, reviewed and updated.
- [ ] A new file, folder or view added to `docs/sync-map.toml`, with its page.
- [ ] A new setting in `.env.example`.
- [ ] A contract or schema change also in the how-to it follows, and in the decision log
      (`AGENTS.md` §14).
- [ ] `uv run --group docs sphinx-build … -W` passes locally, or let CI's `docs` job tell you.
