# Add a setting

Settings are `SATLAB_*` environment variables read by {py:class}`app.config.Settings`
(pydantic-settings, with a `.env` file in development). Thresholds, limits and endpoints belong
here, not in code.

1. **Declare it** in the right group of `backend/app/config.py`, with bounds if it's numeric and a
   `#:` doc-comment. The [settings reference](../reference/settings.md) is generated from these, so
   a group's leading comment (`# Title. Description.`) becomes its section.

   ```python
       # Pass automation. Commands booked for a future pass, sent at AOS if no subsystem is red.
       schedule_lead_s: float = Field(default=60.0, ge=10.0, le=600.0)  #: antenna moves before AOS
   ```

2. **Use it.** Services read `settings.<name>`. A session runtime reads it through
   {py:class}`app.sim.loop.RuntimeConfig`: add a field with the same name and default there, and
   `from_settings` copies it.
3. **Document it for operators.** Add a commented line with the default to `.env.example`.
   `tests/test_docs_sync.py` fails if a setting is missing there. A secret is typed `SecretStr`,
   never logged, and in production lives in the platform's secret store.
4. **Production.** If the default isn't right for production, set it on the API's Container App.
   An image update keeps the app's settings.

The settings reference shows defaults as written in the source and never reads a `.env`, so nothing
local can leak into the public docs.
