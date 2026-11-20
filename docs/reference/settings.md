# Settings

The backend reads its configuration from environment variables (and a `.env` file in local
development) through {py:class}`app.config.Settings`. `.env.example` at the repo root lists them
with commented-out examples; production sets them on the Container App (secrets as secret
references). See [add a setting](../how-to/add-setting.md) to introduce one.

Defaults below are shown as written in the source; nothing here comes from a running server.

```{include} _generated/settings.md
```
