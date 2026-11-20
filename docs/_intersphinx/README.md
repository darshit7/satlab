# Vendored intersphinx inventories

The API reference links types to the Python, NumPy, SQLAlchemy 2.1, pydantic and FastAPI docs.
Their `objects.inv` files are kept here so the docs build is offline and deterministic: a flaky
network can't fail the strict build. Refresh them deliberately (and commit), e.g. when a major
version changes:

```bash
cd docs/_intersphinx
curl -fsSL -o python.inv https://docs.python.org/3/objects.inv
curl -fsSL -o numpy.inv https://numpy.org/doc/stable/objects.inv
curl -fsSL -o sqlalchemy.inv https://docs.sqlalchemy.org/en/21/objects.inv
curl -fsSL -o pydantic.inv https://pydantic.dev/docs/validation/latest/objects.inv
curl -fsSL -o fastapi.inv https://fastapi.tiangolo.com/objects.inv
```

Last refreshed 2026-11-18.
