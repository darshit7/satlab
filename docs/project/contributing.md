# Contributing

## Workflow

- Work happens on `dev`; `main` only receives PRs from `dev`, merged with merge commits, after
  every CI check passes (see [deploy and roll back](../how-to/deploy-and-rollback.md)).
- Build phase by phase ([roadmap](roadmap.md)): don't pull later-phase work forward, and keep every
  phase runnable end to end.
- A change to the schema or the WebSocket contract updates the pydantic models, the TypeScript
  mirror and an Alembic migration together, and gets an entry in the
  [decision log](decisions.md).
- If a change seems to need breaking a [security rule](../architecture/security.md), stop and ask
  rather than working around it.

## Checks

```bash
cd backend  && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd frontend && pnpm lint && pnpm test && pnpm build
cd backend  && uv run --group docs sphinx-build -M dirhtml ../docs ../docs/_build -W --keep-going
```

Never call a change done with a failing ruff, pytest, vitest or docs build.

## Conventions

```{include} ../../AGENTS.md
:start-after: <!-- docs:conventions:start -->
:end-before: <!-- docs:conventions:end -->
```

## Documentation

Docs change with the code, in the same commit. The sync map says which pages a file's change
touches, and CI builds the site strictly. Read [write the docs](../how-to/write-docs.md) and run
through its checklist before you push.

The full rulebook, including the tech stack, repository layout and data sources, is
[`AGENTS.md`](https://github.com/darshit7/satlab/blob/main/AGENTS.md).
