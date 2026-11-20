# Deploy and roll back

Production is two Container Apps (web and API) on Azure, with images in GHCR and the database on
Neon. [Deployment](../architecture/deployment.md) explains the design; this is the procedure.

## Ship a change

1. **Work on `dev`.** Commit and push. CI (`.github/workflows/ci.yml`) runs every check on each
   push: backend lint and tests (SQLite and Postgres), frontend lint, tests and build, the strict
   docs build, and both images built and smoke-tested together.
2. **Open a PR from `dev` to `main`.** `main` accepts PRs only from this repo's `dev`, with the
   required checks green. Merge with a **merge commit** (never squash), so the branches don't
   diverge.
3. **The merge deploys.** `.github/workflows/deploy.yml`:
   1. runs CI again;
   2. builds and pushes `satlab-api` and `satlab-web`, tagged with the commit SHA and `latest`;
   3. runs `alembic upgrade head` on the production database as the schema owner;
   4. updates the API app, then the web app, to the new SHA;
   5. runs `scripts/smoke.sh` against the public URL.

Before merging, check that any migration is expand/contract-safe
([add a migration](add-migration.md)): it runs before the new code, against the old code still
serving.

## Roll back

Point both apps back at the last good SHA (the image tags in GHCR are the commit SHAs):

```bash
az containerapp update -n <app> -g <resource group> --image ghcr.io/darshit7/satlab-api:<older sha>
az containerapp update -n <app> -g <resource group> --image ghcr.io/darshit7/satlab-web:<older sha>
```

An image update keeps each app's settings and secrets. **Migrations are not rolled back**: the old
code must work with the new schema, which expand/contract guarantees. If a migration itself was
wrong, fix forward with a new revision.

## Build and run the images locally

The web image builds from the repo root, because it also builds these docs from `docs/` and the
backend's code:

```bash
docker build -t satlab-api:local backend
docker build -t satlab-web:local -f frontend/Dockerfile .
docker network create satlab
docker run -d --name api --network satlab -p 8000:8000 -e SATLAB_AUTO_MIGRATE=true \
  -e SATLAB_CATALOG_AUTOLOAD=false -e SATLAB_DOCS_ENABLED=false \
  -e 'SATLAB_CORS_ORIGINS=["http://localhost:8080"]' satlab-api:local
docker run -d --name web --network satlab -p 8080:8080 -e SATLAB_API_UPSTREAM=http://api:8000 satlab-web:local
scripts/smoke.sh http://localhost:8080 http://localhost:8000 http://localhost:8080
```

The one-time Azure, GitHub and Neon setup (identities, secrets, rulesets, roles) is kept in
private runbooks outside the repo.
