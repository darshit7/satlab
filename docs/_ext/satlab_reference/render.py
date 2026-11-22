"""The generated reference pages, rendered from code as MyST Markdown.

Each ``*_page`` function returns the body of one page under ``docs/reference/_generated/``; the
hand-written pages in ``docs/reference/`` include them. They read the code, never a running
configuration: settings defaults come from the source text, so no ``.env`` value or machine path
can reach the public site.
"""

import ast
import contextlib
import json
import re
import typing
from pathlib import Path

from pydantic import BaseModel

from .markdown import bullets, code, heading, list_table, paragraphs, prose, text
from .models import attr_docs, model_section, nested_models, own_doc

BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"


class ReferenceError(Exception):
    """The code and what the generator expects of it disagree."""


def _source(node: ast.AST, src: str) -> str:
    return ast.get_source_segment(src, node) or ""


# ---------- settings ----------

_BOUND_KEYWORDS = {"ge": "≥", "gt": ">", "le": "≤", "lt": "<"}


def _split_title(comment: str) -> tuple[str, str]:
    """``"Title. More text"`` → ``("Title", "More text")``."""
    m = re.match(r"(.+?)[.:](\s+|$)", comment)
    if not m:
        return comment, ""
    return m.group(1).strip(), comment[m.end() :].strip()


def settings_page() -> str:
    """Every setting: env var, type, default (as written), bounds and its ``#:`` doc."""
    from app import config

    path = Path(config.__file__)
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    cls = next(
        n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Settings"
    )
    docs = attr_docs("app.config")
    prefix = config.Settings.model_config.get("env_prefix", "")

    groups: list[tuple[str, str, list[str]]] = [("General", "", [])]
    names: list[str] = []
    prev_end = cls.body[0].lineno - 1
    for node in cls.body:
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            prev_end = node.end_lineno or node.lineno
            continue
        name = node.target.id
        names.append(name)
        comment = [
            line.strip().removeprefix("#").strip()
            for line in lines[prev_end : node.lineno - 1]
            if line.strip().startswith("#") and not line.strip().startswith("#:")
        ]
        if comment:
            title, intro = _split_title(" ".join(comment))
            groups.append((title, intro, []))
        prev_end = node.end_lineno or node.lineno

        annotation = _source(node.annotation, src)
        meta = [f"type {code(annotation)}"]
        value = node.value
        bounds: list[str] = []
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "Field"
        ):
            kw = {k.arg: k.value for k in value.keywords if k.arg}
            value = kw.get("default")
            if "default_factory" in kw:
                meta.append(f"default from {code(_source(kw['default_factory'], src))}")
            for key, symbol in _BOUND_KEYWORDS.items():
                if key in kw:
                    bounds.append(f"{symbol} {code(_source(kw[key], src))}")
        if value is not None:
            meta.append(f"default {code(_source(value, src))}")
        elif not any(m.startswith("default from") for m in meta):
            meta.append("required")
        meta.extend(bounds)
        if "SecretStr" in annotation:
            meta.append("**secret**: set it in the environment or a secret store, never in git")
        doc = docs.get(("Settings", name), "")
        doc = doc[:1].upper() + doc[1:]  # a trailing #: comment is a lowercase fragment
        groups[-1][2].append(
            f"{code(prefix + name.upper())}\n: {prose(doc) if doc else 'Undocumented.'}\n\n"
            f"  {' · '.join(meta)}\n"
        )

    declared = list(config.Settings.model_fields)
    if names != declared:
        raise ReferenceError(f"Settings fields parsed {names}, declared {declared}")

    out = [
        f"All settings are environment variables with the prefix {code(prefix)} (or entries "
        f"in a {code('.env')} file); {len(names)} in all, from "
        f"{{py:class}}`app.config.Settings`.\n"
    ]
    for title, intro, entries in groups:
        if not entries:
            continue
        out.append(heading(2, text(title)))
        if intro:
            out.append(prose(intro) + "\n")
        out.append("\n".join(entries))
    return "\n".join(out)


# ---------- REST ----------


def _python_models(*modules: object) -> dict[str, type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__  # type: ignore[attr-defined]
            ):
                found[value.__name__] = value
    return found


def rest_page() -> str:
    """Every REST operation from the app's OpenAPI document, and the models it uses."""
    from app.main import app as api
    from app.models import api as api_models
    from app.models import ws as ws_models

    spec = api.openapi()
    known = _python_models(api_models, ws_models)
    used: list[type[BaseModel]] = []

    def model_link(cls: type[BaseModel]) -> str:
        if cls not in used:
            used.append(cls)
        return f"{{ref}}`{cls.__name__} <rest-{cls.__name__.lower()}>`"

    def schema_text(s: dict[str, typing.Any]) -> str:
        if "$ref" in s:
            name = s["$ref"].rsplit("/", 1)[-1]
            return model_link(known[name]) if name in known else code(name)
        for key in ("anyOf", "oneOf"):
            if key in s:
                return " \\| ".join(schema_text(x) for x in s[key])
        if "allOf" in s and len(s["allOf"]) == 1:
            return schema_text(s["allOf"][0])
        if "const" in s:
            return code(json.dumps(s["const"]))
        if "enum" in s:
            return " \\| ".join(code(json.dumps(v)) for v in s["enum"])
        kind = s.get("type")
        if kind == "array":
            return f"list of {schema_text(s.get('items', {}))}"
        if kind == "null":
            return code("null")
        if kind == "object" and isinstance(s.get("additionalProperties"), dict):
            return f"object of {schema_text(s['additionalProperties'])}"
        base = {"integer": "int", "number": "float", "string": "str", "boolean": "bool"}.get(
            kind or "", kind or "any"
        )
        if s.get("format"):
            base += f" ({s['format']})"
        bounds = [
            f"{symbol} {s[key]!r}"
            for key, symbol in (
                ("minimum", "≥"),
                ("exclusiveMinimum", ">"),
                ("maximum", "≤"),
                ("exclusiveMaximum", "<"),
                ("minLength", "min length"),
                ("maxLength", "max length"),
            )
            if key in s
        ]
        return code(base) + (f" ({', '.join(bounds)})" if bounds else "")

    out = [
        f"{len(spec['paths'])} paths, generated from the API's OpenAPI document "
        f"(FastAPI {code('app.openapi()')}). Paths are as the API serves them; through the web "
        f"app they sit under {code('/satlab')}, e.g. {code('/satlab/api/health')}.\n"
    ]
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            label = "rest-op-" + re.sub(r"[^a-z0-9]+", "-", f"{method} {path}".lower()).strip("-")
            out.append(heading(2, code(f"{method.upper()} {path}"), label))
            out.append(paragraphs(op.get("description")) or text(op.get("summary", "")) + "\n")
            params = op.get("parameters", [])
            if params:
                out.append(
                    list_table(
                        ["Parameter", "In", "Type", "Required", "Description"],
                        [
                            [
                                code(p["name"]),
                                text(p["in"]),
                                schema_text(p.get("schema", {})),
                                "yes" if p.get("required") else "no",
                                prose(p.get("description", "")),
                            ]
                            for p in params
                        ],
                    )
                )
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body:
                out.append(f"Request body (JSON): {schema_text(body.get('schema', {}))}\n")
            rows = []
            for status, response in op.get("responses", {}).items():
                content = response.get("content", {}).get("application/json")
                rows.append(
                    [
                        code(status),
                        text(response.get("description", "")),
                        schema_text(content.get("schema", {})) if content else "",
                    ]
                )
            out.append(list_table(["Status", "Meaning", "Body (JSON)"], rows))

    out.append(heading(2, "Models", "rest-models"))
    all_models = used + [m for m in nested_models(used) if m not in used]
    for cls in all_models:
        out.append(model_section(cls, 3, f"rest-{cls.__name__.lower()}", model_link))
    return "\n".join(out)


# ---------- WebSocket ----------


def _message_type(cls: type[BaseModel]) -> str:
    return str(cls.model_fields["type"].default)


def _union_members(union: object) -> list[type[BaseModel]]:
    if typing.get_origin(union) is typing.Annotated:
        union = typing.get_args(union)[0]
    return list(typing.get_args(union))


def websocket_page() -> str:
    """Close codes, then every client and server message, then the types they share."""
    from app import main
    from app.models import ws

    def link(cls: type[BaseModel]) -> str:
        return f"{{ref}}`{cls.__name__} <ws-{cls.__name__.lower()}>`"

    src = Path(main.__file__).read_text(encoding="utf-8")
    docs = attr_docs("app.main")
    codes = [
        [
            code(_source(node.value, src)),
            code(node.targets[0].id),
            prose(docs.get(("", node.targets[0].id), "")),
        ]
        for node in ast.parse(src).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id.startswith("WS_CLOSE_")
    ]
    if not codes:
        raise ReferenceError("no WS_CLOSE_* constants in app/main.py")

    client = [ws.Auth, *_union_members(ws.ClientCommand)]
    server = _union_members(ws.ServerMessage)
    out = [heading(2, "Close codes", "ws-close-codes")]
    out.append(list_table(["Code", "Constant", "Meaning"], codes))
    out.append(heading(2, "Client → server", "ws-client-messages"))
    out.append(bullets(f"{code(_message_type(m))}: {link(m)}" for m in client))
    for cls in client:
        out.append(
            model_section(cls, 3, f"ws-{cls.__name__.lower()}", link, code(_message_type(cls)))
        )
    out.append(heading(2, "Server → client", "ws-server-messages"))
    out.append(bullets(f"{code(_message_type(m))}: {link(m)}" for m in server))
    for cls in server:
        out.append(
            model_section(cls, 3, f"ws-{cls.__name__.lower()}", link, code(_message_type(cls)))
        )
    out.append(heading(2, "Shared types", "ws-shared-types"))
    for cls in nested_models([*client, *server]):
        out.append(model_section(cls, 3, f"ws-{cls.__name__.lower()}", link))
    return "\n".join(out)


# ---------- database ----------


_ER_TYPES = {
    "int": "int",
    "str": "text",
    "float": "float",
    "bool": "bool",
    "datetime": "timestamp",
    "bytes": "blob",
    "dict": "json",
    "list": "json",
}


def _type_word(column: object) -> str:
    """A dialect-neutral type word for the ER diagram (Mermaid accepts only simple words)."""
    try:
        name = column.type.python_type.__name__  # type: ignore[attr-defined]
    except NotImplementedError:
        name = ""
    return _ER_TYPES.get(name, "value")


def database_page() -> str:
    """Tables, columns (both dialects), keys and indexes from the ORM, an ER diagram and the
    migration history."""
    from sqlalchemy.dialects import postgresql, sqlite

    import app.db.models  # noqa: F401 (registers the mappers on Base)
    from app.db.base import Base

    docs = attr_docs("app.db.models")
    classes = {m.local_table.name: m.class_ for m in Base.registry.mappers}
    tables = list(Base.metadata.sorted_tables)

    diagram = ["erDiagram"]
    for table in tables:
        for fk in table.foreign_keys:
            col = fk.parent
            parent = fk.column.table.name
            left = "|o" if col.nullable else "||"
            right = "o|" if col.unique else "o{"
            diagram.append(f'    {parent} {left}--{right} {table.name} : "{col.name}"')
    for table in tables:  # key columns only: the tables below list every column
        diagram.append(f"    {table.name} {{")
        for col in table.columns:
            keys = [k for k, on in (("PK", col.primary_key), ("FK", col.foreign_keys)) if on]
            if col.unique and not col.primary_key:
                keys.append("UK")
            if keys:
                diagram.append(f"        {_type_word(col)} {col.name} {','.join(keys)}")
        diagram.append("    }")

    out = [
        f"{len(tables)} tables, generated from the SQLAlchemy models in "
        f"{{py:mod}}`app.db.models` (the source of truth for the schema; every change is an "
        f"Alembic migration). Types are shown as SQLite (local and tests) and Postgres (Neon) "
        f"create them.\n",
        heading(2, "Entity relationships", "db-er-diagram"),
        "Keys only; each table's full column list follows. The diagram opens full screen from its "
        "corner button.\n",
        "```{mermaid}\n" + "\n".join(diagram) + "\n```\n",
    ]
    for table in tables:
        cls = classes.get(table.name)
        out.append(heading(2, code(table.name), f"db-{table.name.replace('_', '-')}"))
        if cls is not None:
            out.append(f"{{py:class}}`{cls.__module__}.{cls.__qualname__}`\n")
            out.append(paragraphs(own_doc(cls)))
        rows = []
        for col in table.columns:
            keys = []
            if col.primary_key:
                keys.append("primary key")
            for fk in col.foreign_keys:
                keys.append(f"→ {code(fk.target_fullname)}")
            if col.unique:
                keys.append("unique")
            default = ""
            if col.server_default is not None:
                default = code(getattr(col.server_default, "arg", col.server_default))
            elif col.default is not None and not callable(col.default.arg):
                default = code(repr(col.default.arg))
            elif col.default is not None:
                default = f"set by the app ({code(col.default.arg.__name__)})"
            key = (cls.__qualname__, col.key) if cls is not None else ("", "")
            rows.append(
                [
                    code(col.name),
                    code(col.type.compile(dialect=sqlite.dialect())),
                    code(col.type.compile(dialect=postgresql.dialect())),
                    "yes" if col.nullable else "no",
                    ", ".join(keys),
                    default,
                    prose(docs.get(key, "")),
                ]
            )
        out.append(
            list_table(
                ["Column", "SQLite", "Postgres", "Null", "Keys", "Default", "Description"], rows
            )
        )
        constraints = [
            f"index {code(ix.name)} on {', '.join(code(c.name) for c in ix.columns)}"
            + (" (unique)" if ix.unique else "")
            for ix in sorted(table.indexes, key=lambda ix: ix.name or "")
        ]
        constraints += [
            f"unique {', '.join(code(c.name) for c in uc.columns)}"
            for uc in table.constraints
            if type(uc).__name__ == "UniqueConstraint" and len(uc.columns) > 1
        ]
        if constraints:
            out.append(bullets(constraints))

    out.append(heading(2, "Migration history", "db-migrations"))
    out.append(list_table(["Revision", "Created", "Change"], _migrations()))
    return "\n".join(out)


def _migrations() -> list[list[str]]:
    """Alembic revisions, oldest first, read from their source (nothing is imported or run)."""
    found: dict[str | None, tuple[str, str, str]] = {}
    for path in sorted((BACKEND_DIR / "migrations" / "versions").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = ast.get_docstring(tree) or ""
        values: dict[str, object] = {}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            elif isinstance(node, ast.Assign):
                target, value = node.targets[0], node.value
            else:
                continue
            if isinstance(target, ast.Name):
                with contextlib.suppress(ValueError):
                    values[target.id] = ast.literal_eval(value)
        created = re.search(r"Create Date:\s*(\S+)", doc)
        down = values.get("down_revision")
        found[down if isinstance(down, str) else None] = (
            str(values.get("revision")),
            created.group(1) if created else "",
            doc.splitlines()[0] if doc else "",
        )
    rows, current = [], None
    while current in found:
        revision, created, message = found[current]
        rows.append([code(revision), text(created), text(message)])
        current = revision
    if len(rows) != len(found):
        raise ReferenceError("migrations don't form one linear history")
    return rows


# ---------- seed data ----------


def seed_page() -> str:
    """The versioned seed data, through the app's own validated loaders."""
    from app.data.classify import _RulesFile
    from app.data.seed import SEED_DIR, link_profile_specs, packet_template_specs
    from app.sar.params import change_detection_params
    from app.sim.subsystems import subsystem_models

    out = [
        "The reference data the backend loads at startup, from "
        f"{code('backend/app/data/seed/')}. Every value that drives the physics is cited; the "
        "seeds are validated strictly when loaded, so what is shown here is what runs.\n"
    ]

    stations = json.loads((SEED_DIR / "ground_stations.json").read_text(encoding="utf-8"))
    out.append(heading(2, "Ground stations", "seed-ground-stations"))
    out.append(prose(stations.get("_comment", "")) + "\n")
    out.append(
        list_table(
            ["Station", "Latitude °", "Longitude °", "Altitude m"],
            [
                [text(s["name"]), text(s["lat_deg"]), text(s["lon_deg"]), text(s.get("alt_m", 0))]
                for s in stations["ground_stations"]
            ],
        )
    )

    links = link_profile_specs()
    out.append(heading(2, "Link profiles", "seed-link-profiles"))
    out.append(
        f"Seed version {links.version}, verified {text(links.verified_on)}. Each is a published "
        "downlink budget; the tests reproduce every source's own worked margin (the checks).\n"
    )
    for p in links.profiles:
        out.append(heading(3, f"{text(p.satellite_name)} ({code(p.norad_id)})"))
        out.append(text(p.label) + "\n")
        out.append(
            list_table(
                ["Quantity", "Value"],
                [
                    ["Frequency", f"{p.frequency_hz / 1e6:g} MHz"],
                    ["EIRP", f"{p.eirp_dbw:g} dBW"],
                    [
                        "EIRP pattern",
                        f"{len(p.eirp_pattern)} points" if p.eirp_pattern else "isotropic",
                    ],
                    ["Station G/T", f"{p.station_gt_db_k:g} dB/K"],
                    ["System loss", f"{p.system_loss_db:g} dB"],
                    [
                        "Atmospheric loss",
                        f"{p.atmospheric_loss_db:g} dB at {p.atmospheric_ref_elev_deg:g}°",
                    ],
                    ["Required Eb/N0", f"{p.required_ebn0_db:g} dB"],
                    ["Data rate", f"{p.data_rate_bps:g} bit/s"],
                ],
            )
        )
        out.append("Sources:\n\n" + bullets(f"{text(s.title)}: <{s.url}>" for s in p.sources))
        if p.derivations:
            out.append(
                "Derived values:\n\n"
                + bullets(f"{code(k)}: {prose(v)}" for k, v in p.derivations.items())
            )
        if p.checks:
            out.append(
                list_table(
                    ["Check (from the source)", "Range km", "Elevation °", "Margin dB"],
                    [
                        [text(c.where), text(c.range_km), text(c.elevation_deg), text(c.margin_db)]
                        for c in p.checks
                    ],
                )
            )

    templates = packet_template_specs()
    out.append(heading(2, "Packet templates", "seed-packet-templates"))
    out.append(f"Seed version {templates.version}, verified {text(templates.verified_on)}.\n")
    out.append(
        list_table(
            ["Satellite", "NORAD", "Template set", "Downlink", "Sources"],
            [
                [
                    text(s.satellite_name),
                    code(s.norad_id),
                    code(s.template_set),
                    f"{s.downlink_frequency_hz / 1e6:g} MHz" if s.downlink_frequency_hz else "",
                    " ".join(f"<{r.url}>" for r in s.downlink_sources),
                ]
                for s in templates.satellites
            ]
            + (
                [
                    [
                        "Every other satellite (debris and rocket bodies excepted)",
                        "",
                        code(templates.generic.template_set),
                        "",
                        "",
                    ]
                ]
                if templates.generic
                else []
            ),
        )
    )
    if templates.generic:
        out.append(
            f"The generic set assumes a TT&C antenna covering "
            f"{templates.generic.antenna_half_angle_deg:g}° off nadir (90°: omnidirectional), "
            "which the attitude check uses for those satellites. It's stored once, with no "
            "satellite.\n"
        )
    for name, tset in templates.template_sets.items():
        out.append(heading(3, code(name)))
        rows = [
            [
                code(t.name),
                text(t.label),
                code(t.framing.type),
                code(t.payload.encoding),
                f"every {t.interval_s:g} s" if t.interval_s else "",
                text(t.downlink),
                "sourced" if t.layout_sourced else "illustrative",
            ]
            for t in tset.telemetry
        ]
        out.append(
            list_table(
                ["Telemetry", "Label", "Framing", "Encoding", "Interval", "Downlink", "Layout"],
                rows,
            )
        )
        if tset.commands:
            out.append(
                list_table(
                    ["Command", "Label", "Parameters"],
                    [
                        [
                            code(c.name),
                            text(c.label),
                            ", ".join(code(p.name) for p in c.params),
                        ]
                        for c in tset.commands
                    ],
                )
            )
        sources = {s.url: s.title for t in tset.telemetry for s in t.sources}
        out.append("Sources:\n\n" + bullets(f"{text(t)}: <{u}>" for u, t in sources.items()))

    rules = _RulesFile.model_validate_json((SEED_DIR / "satellite_types.json").read_bytes()).rules
    out.append(heading(2, "Satellite type rules", "seed-type-rules"))
    out.append(
        "Applied in order to each catalogued satellite's name (and NORAD id); the first match "
        "wins, anything unmatched is " + code("other") + ".\n"
    )
    out.append(
        list_table(
            ["#", "Type", "SAR", "No radio", "Name patterns", "NORAD ids"],
            [
                [
                    str(i),
                    code(r.type),
                    "yes" if r.is_sar_capable else "",
                    "yes" if r.no_radio else "",
                    ", ".join(code(p) for p in r.patterns),
                    ", ".join(code(n) for n in r.norad_ids),
                ]
                for i, r in enumerate(rules, 1)
            ],
        )
    )

    models = subsystem_models()
    out.append(heading(2, "Subsystem models", "seed-subsystem-models"))
    out.append(
        f"Seed version {models.version}. Generic, labelled-as-modelled parameters; a type entry "
        "overrides the default field by field.\n"
    )
    default = models.default.model_dump()
    kinds = list(models.by_type)
    rows = []
    for section, fields in default.items():
        for field, value in fields.items():
            overrides = [models.by_type[k].model_dump()[section][field] for k in kinds]
            rows.append(
                [code(f"{section}.{field}"), text(value)]
                + [text(v) if v != value else "" for v in overrides]
            )
    out.append(list_table(["Parameter", "Default", *(code(k) for k in kinds)], rows))
    thresholds = models.thresholds.model_dump()
    out.append(
        "Thresholds:\n\n"
        + bullets(f"{code(k)}: {code(json.dumps(v))}" for k, v in thresholds.items())
    )

    detection = change_detection_params()
    out.append(heading(2, "SAR change detection", "seed-sar-change-detection"))
    out.append(prose(" ".join(detection.comment)) + "\n")
    out.append(f"Seed version {detection.version}.\n")
    params = detection.model_dump(exclude={"comment", "version"})
    rows = []
    for section, fields in params.items():
        for field, value in fields.items():
            rows.append([code(f"{section}.{field}"), text(json.dumps(value))])
    out.append(list_table(["Parameter", "Value"], rows))
    return "\n".join(out)


PAGES = {
    "settings.md": settings_page,
    "rest.md": rest_page,
    "websocket.md": websocket_page,
    "database.md": database_page,
    "seed-data.md": seed_page,
}
