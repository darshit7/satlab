"""Pydantic models as reference sections: fields, types, bounds, defaults and ``#:`` docs.

Field docs are the ``#:`` doc-comments next to each field (AGENTS.md §6), read from the source by
Sphinx's ``ModuleAnalyzer``: the same text autodoc shows in the API reference.
"""

import inspect
import json
import types
import typing
from collections.abc import Callable
from datetime import date, datetime
from functools import cache

from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen
from pydantic import AwareDatetime, BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from sphinx.pycode import ModuleAnalyzer

from .markdown import code, heading, list_table, paragraphs, prose

Link = Callable[[type[BaseModel]], str]

_NAMES: dict[object, str] = {
    str: "str",
    int: "int",
    float: "float",
    bool: "bool",
    bytes: "bytes",
    datetime: "datetime",
    AwareDatetime: "datetime (with time zone)",
    date: "date",
    typing.Any: "any",
}


@cache
def attr_docs(module: str) -> dict[tuple[str, str], str]:
    """``#:`` doc-comments (and attribute docstrings) of a module, by (class, attribute)."""
    found = ModuleAnalyzer.for_module(module).find_attr_docs()
    return {key: " ".join(line.strip() for line in lines).strip() for key, lines in found.items()}


def field_doc(cls: type, name: str) -> str:
    """The doc of field ``name``, from the class that declares it (inherited fields too)."""
    for klass in cls.__mro__:
        if klass is BaseModel or klass is object:
            break
        doc = attr_docs(klass.__module__).get((klass.__qualname__, name))
        if doc:
            return doc
    return ""


def own_doc(cls: type) -> str | None:
    """The class's own docstring: not one inherited from BaseModel."""
    doc = cls.__dict__.get("__doc__")
    return inspect.cleandoc(doc) if doc else None


def constraints(metadata: typing.Iterable[object]) -> list[str]:
    """Human-readable bounds from a field's (or an ``Annotated`` type's) metadata."""
    out: list[str] = []
    for m in metadata:
        for kind, symbol in ((Ge, "≥"), (Gt, ">"), (Le, "≤"), (Lt, "<")):
            if isinstance(m, kind):
                out.append(f"{symbol} {getattr(m, kind.__name__.lower())!r}")
        if isinstance(m, MinLen):
            out.append(f"min length {m.min_length}")
        if isinstance(m, MaxLen):
            out.append(f"max length {m.max_length}")
        pattern = getattr(m, "pattern", None)
        if isinstance(pattern, str):
            out.append(f"pattern {code(pattern)}")
        if getattr(m, "strict", None) is True or type(m).__name__ == "Strict":
            out.append("strict")
    return out


def _with_bounds(base: str, bounds: list[str]) -> str:
    return f"{base} ({', '.join(bounds)})" if bounds else base


def type_text(tp: object, link: Link) -> str:
    """A field type as Markdown: unions, literals, lists and models (linked)."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if tp is type(None):
        return code("null")
    if origin is typing.Annotated:
        return _with_bounds(type_text(args[0], link), constraints(args[1:]))
    if origin in (typing.Union, types.UnionType):
        return " \\| ".join(type_text(a, link) for a in args)
    if origin is typing.Literal:
        return " \\| ".join(code(json.dumps(a)) for a in args)
    if origin in (list, set, frozenset) or (origin is tuple and len(args) == 2 and args[1] is ...):
        return f"list of {type_text(args[0], link)}"
    if origin is tuple:
        return f"[{', '.join(type_text(a, link) for a in args)}]"
    if origin is dict:
        return f"object: {type_text(args[0], link)} → {type_text(args[1], link)}"
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return link(tp)
    name = _NAMES.get(tp) or getattr(tp, "__name__", None) or repr(tp)
    return code(name)


def default_text(field: FieldInfo) -> str:
    """A field's default as JSON (the wire format), or "required"."""
    if field.default_factory is not None:
        try:
            value = field.default_factory()  # type: ignore[call-arg]
        except TypeError:
            return "computed"
    elif field.default is PydanticUndefined:
        return "required"
    else:
        value = field.default
    return code(json.dumps(value, ensure_ascii=False, default=str))


def nested_models(roots: typing.Iterable[type[BaseModel]]) -> list[type[BaseModel]]:
    """Models reachable from the roots' field types, in order of first use (roots excluded)."""
    roots = list(roots)
    seen: list[type[BaseModel]] = []

    def visit(tp: object) -> None:
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            if tp in seen:
                return
            seen.append(tp)
            for f in tp.model_fields.values():
                visit(f.annotation)
            return
        for arg in typing.get_args(tp):
            visit(arg)

    for root in roots:
        for f in root.model_fields.values():
            visit(f.annotation)
    return [m for m in seen if m not in roots]


def model_section(
    cls: type[BaseModel], level: int, label: str, link: Link, title: str | None = None
) -> str:
    """One model: heading, Python cross-reference, docstring and a field table."""
    rows = [
        [
            code(f.alias or name),
            _with_bounds(type_text(f.annotation, link), constraints(f.metadata)),
            default_text(f),
            prose(field_doc(cls, name)),
        ]
        for name, f in cls.model_fields.items()
    ]
    parts = [
        heading(level, title or code(cls.__name__), label),
        f"{{py:class}}`{cls.__module__}.{cls.__qualname__}`\n",
        paragraphs(own_doc(cls)),
        list_table(["Field", "Type", "Default", "Description"], rows) if rows else "No fields.\n",
    ]
    return "\n".join(p for p in parts if p) + "\n"
