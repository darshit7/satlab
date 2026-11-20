# Database schema

SQLite in local development and tests, PostgreSQL on Neon in production: the same SQLAlchemy models
create both, and every change is an Alembic migration that works on both
([persistence](../architecture/persistence.md), [add a migration](../how-to/add-migration.md)).

```{include} _generated/database.md
```
