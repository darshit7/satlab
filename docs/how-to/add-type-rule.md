# Add a satellite type rule

Every catalogued satellite gets a type (`station`, `weather`, `comms`, `navigation`, `sar`, `other`)
and a SAR-capable flag. They come from ordered name-pattern rules in
`backend/app/data/seed/satellite_types.json`. The UI labels the type `comms` "Telecom", so that
"Communications" means one thing on screen.

1. **Edit the rules.** Each rule has a `type`, an optional `is_sar_capable`, an optional
   `no_radio` (debris and rocket bodies: no generic packet templates, so the station can't contact
   them), case-insensitive regex `patterns` and explicit `norad_ids`. **The first match wins**, so
   order matters: debris and rocket bodies come first, and specific traps come before broad
   patterns.
2. **Test the traps.** `backend/tests/test_classify.py` pins the cases that look alike but aren't.
   SENTINEL-2 is not SAR; SARAL is not SAR; GSAT0xxx is Galileo (navigation) while GSAT-xx is ISRO
   (Telecom). Add yours there.

   ```bash
   cd backend && uv run pytest tests/test_classify.py
   ```

3. **No migration needed.** The rules are applied on every upsert and re-applied to the whole
   catalog at startup ({py:func}`app.data.classify.reclassify_all`), so editing them reclassifies
   existing rows on the next start.

The {ref}`seed data reference <seed-type-rules>` lists the rules in order.
