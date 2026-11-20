# Add a migration

The ORM models in {py:mod}`app.db.models` are the schema's source of truth. Every change to them
ships with an Alembic revision, and that revision must work on SQLite and on Postgres.

## 1. Change the model, generate the revision

```bash
cd backend
# edit app/db/models.py (with #: docs on new columns)
uv run alembic revision --autogenerate -m "short description"
```

Read the generated file in `backend/migrations/versions/`. Autogenerate misses things: server
defaults, renamed columns (which it sees as drop and add), and SQLite's limited `ALTER TABLE`. Use
`op.batch_alter_table` for SQLite-safe changes.

## 2. Keep it expand/contract

Migrations run in production **before** the new code rolls out, and they are never rolled back
automatically. The code that is still running must therefore work with the new schema:

- **Expand** first: add a nullable column (or one with a server default), a new table, a new
  index. Ship code that writes and reads it.
- **Contract** later, in a separate release, once nothing reads the old shape: drop the old
  column, tighten the constraint.
- Never rename in one step; add, backfill, switch, then drop.

## 3. Test on both dialects

```bash
uv run pytest tests/test_migrations.py                         # SQLite
SATLAB_TEST_POSTGRES_URL=… uv run pytest tests/test_migrations.py  # Postgres (or via .env.test)
```

Locally, `uvicorn` applies migrations on startup (`SATLAB_AUTO_MIGRATE`). To try an unreleased
migration without touching a shared dev database, point both database URLs at a scratch SQLite
file. In production the deploy workflow runs `alembic upgrade head` as the schema owner.

## Docs to update

- The [database reference](../reference/database.md) regenerates, with the new ER diagram and the
  migration in the history.
- Update [persistence](../architecture/persistence.md) if what is stored, or why, changed.
